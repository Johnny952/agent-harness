# ia-harness — Diseño de arquitectura

> **Estado: EN PROGRESO.** Este documento se actualiza de forma incremental a
> medida que se aprueban secciones del diseño (a pedido explícito del
> usuario, en vez de escribirse recién al final del proceso de
> brainstorming). Las secciones marcadas **[Aprobada]** son firmes; el resto
> del proceso (auto-revisión del spec, revisión final del usuario,
> transición a `writing-plans`) ocurre solo cuando todas las secciones estén
> aprobadas — ver `## Pendiente` al final.

## Resumen

Plataforma multiagente autónoma, persistente y 24/7 para orquestar un flujo
de desarrollo en cascada (Arquitecto → Implementador → Revisor → Auditor)
usando múltiples instancias de Claude Code, cada una atada a una sesión
OAuth legítima de una suscripción Claude Pro distinta. Corre íntegramente en
un servidor Ubuntu local, ya usado por Coolify, expuesto de forma privada
vía Tailscale.

Documento origen: `proyecto.md` (propuesta inicial). Este spec reemplaza sus
decisiones de diseño donde difieran (p. ej. la UI de control) y es ahora la
fuente de verdad completa y autocontenida; `proyecto.md` fue eliminado del
repo tras la aprobación de este documento.

## Decisiones de alcance

- **Escala objetivo:** 2 cuentas Claude Pro en uso inmediato, pero el diseño
  debe soportar N cuentas sin cambios estructurales.
- **Backlog:** vive únicamente en la herramienta de control (Vibe Kanban).
  No hay una fuente de verdad paralela (issues de GitHub, Markdown suelto,
  etc.) para el trabajo en curso.
- **Alcance actual vs. futuro:** ver sección 8 — el workflow implementado
  ahora es serial (una cuenta a la vez); paralelismo/balanceo de carga
  queda documentado como trabajo futuro, no como parte de este spec.

## 1. Herramienta de control / UI — Vibe Kanban **[Aprobada]**

Evaluadas:

- **Vibe Kanban** — elegida. Docker-nativo, orquesta vía `docker exec`, encaja
  directo con el modelo de contenedores por agente.
- **Conductor.build** — descartada: solo Mac, y el servidor objetivo es
  Ubuntu. `proyecto.md` la nombraba porque fue el punto de partida de la
  idea, pero no es viable en este entorno.
- **munder difflin** — descartada como herramienta a instalar (es Electron,
  solo desktop); se reutiliza como referencia de patrones de UI si hace
  falta más adelante.
- **taskmd** — descartada: no se solapa con lo que necesita este sistema
  (gestión de backlog ya cubierta por Vibe Kanban).

## 2. Modelo de enrutamiento multi-proyecto **[Aprobada]**

- Los contenedores son **stateless**: no tienen un proyecto "fijo" de
  fábrica.
- La identidad de proyecto/tarea de una sesión de trabajo queda fija por la
  forma en que se la invoca: el dispatcher hace
  `docker exec -w /data/projects/<slug>/worktrees/<task-id> <container> claude ...`.
  El `-w` decide sobre qué proyecto y qué tarea trabaja esa ejecución.
- Una sesión de trabajo es sobre **un solo proyecto**.
- **Subproyectos:** se organizan con un `git clone` plano dentro de una
  carpeta de convención `subprojects/` en el repo del proyecto padre.
- Un subproyecto solo obtiene su propio `.hive/` (es decir, su propio ciclo
  Arquitecto→Implementador→Revisor→Auditor independiente) si efectivamente
  corre el flujo de roles completo por separado. Si es solo código
  consumido por el proyecto padre, no necesita `.hive/` propio.

## 3. Volúmenes y separación de credenciales **[Aprobada]**

- Los volúmenes Docker se montan de forma **anidada**: hay un volumen
  `~/.claude` compartido entre todos los contenedores (sesiones, config),
  pero dentro de él, el sub-path de credenciales queda **sombreado** por un
  volumen más específico, distinto por cuenta.
- Resultado: cada contenedor ve el mismo histórico de sesiones y config
  general, pero autentica con una cuenta Claude Pro propia — sin que una
  cuenta pueda leer las credenciales de otra.

## 4. Smart Dispatcher

### 4a. Máquina de estados, detección de cuota y transferencia de contexto **[Aprobada]**

**Máquina de estados por cuenta**, persistida en disco (no en memoria del
proceso del dispatcher, para sobrevivir crashes/reinicios):

- `IDLE` — disponible para recibir tareas.
- `BUSY` — ejecutando una tarea (1 tarea por cuenta a la vez).
- `PRE_COOLDOWN` — cerca del límite de cuota (ver polling proactivo abajo);
  deja de recibir tareas **nuevas**, pero termina la que tiene en curso.
- `COOLING_DOWN` — sin cuota disponible; no recibe tareas hasta liberarse.

**Detección de cuota — proactiva (mecanismo primario):**

`claude -p "/usage" --output-format json` dispara la misma lógica del
comando interactivo `/usage`, pero como `local_command`: no consume turno
facturable (`total_cost_usd: 0`). Su campo `result` trae texto libre con dos
ventanas — uso de sesión y uso semanal — cada una con % usado y timestamp de
reset, p. ej.:

```
Current session: 33% used · resets Sep 13, 10:50pm (America/Santiago)
Current week (all models): 24% used · resets Sep 18, 11am (America/Santiago)
```

El dispatcher lo consulta **just-in-time, antes de despachar cada tarea** a
una cuenta (es gratis, no hace falta un timer separado) y parsea ambos
porcentajes. Al cruzar un umbral configurable (p. ej. 90% de sesión o
semanal), la cuenta pasa a `PRE_COOLDOWN`.

> **Caveat:** `/usage` devuelve texto libre, no una API estructurada
> documentada. El formato debe tratarse como frágil entre versiones del CLI
> — parsear a la defensiva y no asumir que el template no va a cambiar.

**Detección de cuota — reactiva (respaldo):** se mantiene como red de
seguridad para el caso en que el parseo de `/usage` falle o el límite golpee
sin aviso — preferir la salida estructurada JSON/stream-json de Claude Code
sobre scraping de texto libre para detectar el error de rate-limit en sí.
*(Las cadenas de señal exactas siguen sin verificar empíricamente.)*

**Failover:** al entrar una cuenta en `COOLING_DOWN` (proactivo o reactivo),
su tarea en curso se reencola hacia la próxima cuenta `IDLE`, retomando vía
`session_id` (mecanismo 2 abajo). Si todas las cuentas están
cerrando/cerradas, la tarea se marca visiblemente en Vibe Kanban como
"bloqueada, esperando cupo".

**Caso adicional — contenedor caído sin señal limpia:** un crash o cuelgue
de contenedor no dispara ninguno de los dos mecanismos de detección de
cuota anteriores (ni 429 reactivo, ni `/usage` proactivo). Este caso se
cubre con el heartbeat/TTL del lock de `.hive/tasks/<task-id>.md` (ver
mecanismo 1, abajo): si el heartbeat no se refresca dentro del TTL, el
dispatcher considera el contenedor caído, libera el lock y reencola la
tarea hacia la próxima cuenta `IDLE` vía `session_id`/`--resume`
(mecanismo 2), igual que en un failover por cuota — sin esperar
indefinidamente a un contenedor que no va a responder.

**Transferencia de contexto — dos mecanismos según el motivo del handoff:**

1. **Transición entre roles** (Arquitecto → Implementador, etc.): en vez de
   un único `checkpoint.md` de texto libre, cada tarea tiene su propio
   archivo `.hive/tasks/<task-id>.md`:
   - **Frontmatter** de estado: `status` (`pending`/`in_progress`/
     `blocked`/`done`), `owner` (cuenta/contenedor que tiene la tarea
     tomada), `depends_on` (IDs de tareas previas de las que depende).
   - **Cuerpo:** el resumen de handoff (~500 palabras) — mismo espíritu que
     el `checkpoint.md` original, un cold-start deliberado: el rol
     siguiente arranca con contexto al 0%, lo cual es una ventaja (no
     arrastra el razonamiento interno del rol anterior), no una limitación.
   - **Lock con TTL/heartbeat:** al tomar una tarea, el dispatcher escribe
     `owner` y un timestamp de heartbeat en el archivo, que refresca
     periódicamente mientras el contenedor trabaja. Si el heartbeat no se
     actualiza dentro del TTL, el lock se considera expirado (ver "Caso
     adicional" arriba) y la tarea puede reasignarse sin esperar
     indefinidamente a un contenedor colgado o caído.
   - Los commits de Git siguen siendo el mecanismo de persistencia del
     código en sí; `.hive/tasks/<task-id>.md` es solo el archivo de
     control/handoff.
2. **Agotamiento de cuota dentro del mismo rol** (la Cuenta 1 se queda sin
   tokens a mitad de una tarea): perder el contexto acá es puro costo. En su
   lugar:
   - Cada invocación (`claude -p ... --output-format json`) devuelve un
     `session_id` en su salida. El dispatcher lo captura y lo persiste en el
     estado en disco de la tarea (junto al estado de cuenta de arriba, pero
     indexado por tarea).
   - Al reencolar la tarea hacia la próxima cuenta `IDLE`, el dispatcher
     invoca `claude --resume <session_id> -p "..." --output-format json` en
     el contenedor nuevo. La cuenta que retoma ve la conversación completa,
     no un resumen.
   - `.hive/tasks/<task-id>.md` sigue como red de seguridad si la sesión no
     resulta resumible (corrupción, sesión purgada, etc.) — cae al flujo de
     cold-start ya descrito en el punto 1.

   **Evidencia empírica:** se probó `--resume <session_id>` invocando desde
   un directorio de trabajo *distinto* al que generó la sesión, y funcionó
   — recuperó correctamente un dato dado en el turno anterior. A diferencia
   de `-c/--continue` (que sí es local al `cwd`), `--resume` con ID
   explícito solo necesita que el `.jsonl` de la sesión sea visible, lo cual
   ya está garantizado por el volumen `~/.claude` compartido (sección 3).

   > **Caveat:** la prueba se hizo con una sola cuenta autenticada en esta
   > máquina — no hay todavía una segunda cuenta Pro disponible para
   > confirmar el caso realmente cross-account. La evidencia a favor es
   > fuerte por diseño (el almacenamiento de sesión se indexa solo por
   > path, y la autenticación OAuth vive en una capa separada, ya resuelta
   > por el sombreado de volúmenes de la sección 3), pero se recomienda
   > validarlo empíricamente en cuanto la segunda cuenta esté provisionada.

### 4b. Hardening de seguridad para Docker-out-of-Docker (DooD) **[Aprobada]**

- **Enfoque único: sidecar `docker:dind` por agente.** Cada contenedor
  agente tiene su propio contenedor sidecar `docker:dind`, con su propio
  daemon Docker aislado — el agente nunca monta ni ve el socket del host.
  Esto reemplaza tanto el montaje directo de `/var/run/docker.sock` del
  diseño original de `proyecto.md` como cualquier variante de proxy sobre
  el socket del host: se descartó explícitamente `docker-socket-proxy`
  (Tecnativa) como respuesta al problema de permisos de DooD, porque un
  daemon aislado por agente resuelve el aislamiento de forma más directa
  que una allow-list de comandos sobre un socket compartido.
- **Hardening del sidecar:** el `docker:dind` corre bajo runtime `sysbox`
  (`--runtime=sysbox-runc`), **sin** `--privileged` — sysbox provee el
  aislamiento de kernel que normalmente requeriría `--privileged` en un
  DinD clásico, eliminando esa superficie de ataque.
- **Alcance de comandos:** dentro de su propio daemon aislado, el agente
  puede ejecutar libremente `docker compose up/down`, `docker logs`,
  builds, tests, etc. — no hace falta una allow-list de comandos porque el
  daemon en sí ya está confinado al sidecar del agente, sin visibilidad
  de otros agentes ni del host.

**Compartir imágenes/builds entre sidecars sin duplicar disco:**

Cada sidecar `docker:dind` tiene su propio daemon y, por defecto, su
propia cache de capas/imagenes — sin nada más, N agentes construyendo el
mismo proyecto implican N descargas y N builds completos. Para evitarlo:

- **Registry mirror local** (`registry:2` en modo proxy/pull-through
  cache): todos los sidecars apuntan su `registry-mirrors` a este
  registry local; una imagen base descargada por un agente queda cacheada
  y los demás la obtienen del mirror en vez de re-descargarla de Docker
  Hub/registries externos.
- **BuildKit registry cache** (`--cache-to=type=registry
  --cache-from=type=registry` apuntando al mismo registry local): permite
  que las capas de build (no solo las imágenes base) se compartan entre
  sidecars, evitando builds completos repetidos cuando el Dockerfile no
  cambió.
- **Prune periódico:** `docker system prune -af --volumes` corriendo con
  cadencia periódica (p. ej. cron diario) en cada sidecar, para evitar que
  el ahorro de disco del mirror/cache se pierda por acumulación de
  imágenes/volúmenes intermedios sin usar.

## 5. Flujo operativo end-to-end **[Aprobada]**

Traducción del diagrama y la secuencia originales de `proyecto.md` al
vocabulario y mecánica definidos en las secciones 1-4 (Vibe Kanban en vez de
Conductor.build, máquina de estados del dispatcher, doble handoff). No
introduce decisiones nuevas.

**Diagrama actualizado:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│ UBUNTU SERVER (24/7, ya corre Coolify)                                  │
│                                                                           │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │ VIBE KANBAN (UI de control)                                        │ │
│  │ • Backlog, kanban, diffs y aprobaciones — única fuente de verdad   │ │
│  │ • Expuesto de forma privada vía Tailscale                          │ │
│  └─────────────────────────────────┬─────────────────────────────────┘ │
│                                     │ docker exec                        │
│                                     ▼                                    │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │ SMART DISPATCHER                                                   │ │
│  │ • Máquina de estados por cuenta: IDLE / BUSY / PRE_COOLDOWN /      │ │
│  │   COOLING_DOWN (persistida en disco)                               │ │
│  │ • Antes de despachar: poll de `/usage` (just-in-time, gratis)      │ │
│  │ • Resuelve -w según proyecto/tarea; guarda session_id por tarea    │ │
│  └───────┬───────────────────────────────────────────┬───────────────┘ │
│          ▼                                           ▼                  │
│  ┌──────────────────────────┐            ┌──────────────────────────┐  │
│  │ CONTENEDOR AGENTE 1       │            │ CONTENEDOR AGENTE 2       │  │
│  │ Claude CLI (Cuenta Pro 1) │            │ Claude CLI (Cuenta Pro 2) │  │
│  └────────────┬──────────────┘            └─────────────┬─────────────┘  │
│               └───────────────────┬────────────────────┘                │
│                                    ▼                                     │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │ VOLÚMENES                                                          │ │
│  │ • ~/.claude compartido (sesiones+config), sombreado por credencial│ │
│  │   propia de cada cuenta (sección 3)                                │ │
│  │ • /data/projects/<slug>/worktrees/<task-id> (git worktrees)        │ │
│  │ • .hive/tasks/<task-id>.md por tarea (lock+heartbeat, handoff)     │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                     │                                    │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │ SIDECARS docker:dind (uno por agente, DooD — sección 4b)            │ │
│  │ • Daemon Docker aislado por agente, sysbox-runc, sin --privileged   │ │
│  │ • Registry mirror local + BuildKit registry cache (imágenes/build) │ │
│  └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

**Secuencia operativa:**

1. **Definición de tarea:** desde Vibe Kanban se crea/asigna una tarjeta con
   el proyecto (y subproyecto si aplica) destino.
2. **Fase Arquitecto:** el dispatcher, según la máquina de estados, elige
   una cuenta disponible y ejecuta
   `docker exec -w .../worktrees/<task-id> <contenedor> claude -p "..."` con
   rol Arquitecto. Antes de despachar, consultó `/usage` de esa cuenta y
   tomó el lock de `.hive/tasks/<task-id>.md` (heartbeat periódico mientras
   dura la fase). La especificación resultante se escribe en el repo y el
   resumen de handoff en el cuerpo de `.hive/tasks/<task-id>.md`.
3. **Handoff a Implementador:** cold-start deliberado vía
   `.hive/tasks/<task-id>.md` (mecanismo 1 de la sección 4a) — el
   dispatcher libera el lock del Arquitecto, actualiza `status`/`owner` y
   despacha al rol Implementador, mismo u otro contenedor/cuenta, sin
   arrastrar el contexto del Arquitecto.
4. **Durante la implementación, agotamiento de cuota o caída de contenedor
   (caso nuevo):** si la cuenta activa entra en `COOLING_DOWN` (proactivo
   por `/usage` o reactivo por error 429), o si el heartbeat del lock
   expira por TTL (contenedor caído/colgado), el dispatcher reencola la
   tarea hacia la próxima cuenta `IDLE` usando `claude --resume
   <session_id>` (mecanismo 2 de la sección 4a) — sin perder el trabajo en
   curso del rol Implementador. Si el resume falla, cae al resumen de
   `.hive/tasks/<task-id>.md` como red de seguridad.
5. **Fase Revisor/Auditor:** nuevo cold-start vía `.hive/tasks/<task-id>.md`,
   valida el diff contra la especificación del Arquitecto.
6. **Aprobación final:** desde Vibe Kanban (vía Tailscale), se inspecciona
   el resultado y se hace merge a la rama principal.

## 6. Observabilidad **[Aprobada]**

Patrón hooks → HTTP → SQLite → dashboard, para poder ver en un solo lugar
qué está haciendo cada agente sin tener que entrar a cada contenedor.

- **Emisión:** hooks de Claude Code (`PreToolUse`/`PostToolUse`/etc., o
  equivalente) en cada contenedor emiten eventos vía HTTP a un colector
  central. Cada evento se etiqueta con `source_app` = identificador del
  contenedor/cuenta que lo generó, para poder filtrar por agente.
- **Almacenamiento:** SQLite en modo WAL en el colector — suficiente para
  el volumen de eventos de N agentes serializados (no hay escritura
  concurrente masiva porque el workflow actual es serial, sección 8).
- **Consumo:**
  - **Dashboard:** expuesto en la red Tailscale, con su propia
    autenticación (no basta con "está en la Tailnet" — el dashboard puede
    exponer detalles de ejecución de varios proyectos/cuentas a la vez).
  - **Smart Dispatcher:** puede consultar el mismo store SQLite para
    decisiones operativas (p. ej. detectar un agente inactivo hace rato
    como señal adicional a la del heartbeat de `.hive/tasks/<task-id>.md`,
    sección 4a).

## 7. Límites de recursos y convención de ramas **[Aprobada]**

- **Límites de CPU/RAM por contenedor:** cada contenedor agente (y su
  sidecar `docker:dind`, sección 4b) corre con límites explícitos de
  CPU/RAM (`--cpus`, `--memory` o equivalentes en Compose/Swarm). Evita que
  un agente con un build o test colgado acapare recursos del host y
  degrade al resto de los agentes o a las apps de Coolify que conviven en
  el mismo servidor. Valores concretos quedan como detalle de
  implementación (dependen del hardware del servidor y N cuentas activas),
  no del diseño.
- **Convención de ramas/worktrees:** `agent/<rol>/<task-id>`, p. ej.
  `agent/implementador/task-123`. Da trazabilidad directa entre una rama,
  el rol que la generó y la tarea de `.hive/tasks/<task-id>.md` que la
  originó, sin necesidad de cruzar con el dashboard de observabilidad para
  saber de dónde salió un branch.

## 8. Alcance actual / Trabajo futuro **[Aprobada]**

**Alcance actual — workflow serial:**

El diseño descrito en las secciones 1-7 apunta al workflow más simple:
**una cuenta/sesión activa a la vez**. El dispatcher no reparte trabajo en
paralelo entre cuentas; cambia a la siguiente cuenta recién cuando la
actual termina su turno (fin de fase, agotamiento de cuota, o caída de
contenedor — sección 4a). Esto es una decisión deliberada de alcance, no
una limitación técnica del resto del diseño: simplifica el modelo de
concurrencia (sin necesidad de coordinar escrituras simultáneas a
`.hive/tasks/`, sin necesidad de políticas de reparto de carga) para la
escala objetivo de 2 cuentas.

**Trabajo futuro — workflow paralelo/balanceado (fuera de alcance de este spec):**

Queda documentado, sin diseñar en detalle, un modo alternativo
configurable (toggle) de paralelismo/balanceo de carga entre cuentas, con
dos variantes posibles a evaluar cuando se aborde ese trabajo:

- **Subagentes por cuenta según cuota:** el dispatcher spawnea subagentes
  distribuidos entre las cuentas disponibles en función de la cuota
  restante de cada una, en vez de servializar contra una sola cuenta
  activa.
- **Reclamo independiente de tareas con relevo por cuota:** cada
  contenedor/cuenta reclama tareas de forma independiente (sin turnos
  centralizados) y el relevo entre cuentas se dispara por señal de cuota
  (mismo mecanismo de detección proactiva/reactiva de la sección 3), en
  vez de por fin de fase.

Ninguna de las dos variantes se implementa como parte de este spec; se
deja como toggle futuro sobre la misma base (Smart Dispatcher,
`.hive/tasks/<task-id>.md`, sidecars DooD, observabilidad) para no tener
que rediseñar desde cero cuando se aborde.

## Pendiente

- Las 8 secciones del diseño (control/UI, enrutamiento multi-proyecto,
  volúmenes/credenciales, smart dispatcher con sus dos subsecciones —
  incluyendo el hardening de DooD por sidecar `docker:dind` de la sección
  4b—, flujo operativo end-to-end, observabilidad, límites de
  recursos/convención de ramas, y alcance actual/trabajo futuro) están
  **[Aprobada]**. Los 4 pilares originales de `proyecto.md` quedan
  cubiertos.
- Auto-revisión del spec (placeholders, consistencia interna, alcance,
  ambigüedad) — pendiente de ejecutar ahora que el diseño está cerrado.
- Revisión final por parte del usuario del spec completo.
- Recién después de la aprobación final: invocar `writing-plans` para el
  plan de implementación. Ninguna otra acción de implementación está
  autorizada antes de ese punto.
