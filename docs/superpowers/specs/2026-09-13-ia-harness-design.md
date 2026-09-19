# ia-harness — Diseño de arquitectura

> **Estado: APROBADO.** Las 8 secciones quedaron **[Aprobada]**, la
> auto-revisión del spec ya se ejecutó, y el usuario aprobó el documento
> completo ("Apruebo la definición"). `proyecto.md` fue eliminado porque este
> spec lo reemplaza como única fuente de verdad. El siguiente paso —
> transición a `writing-plans` para producir el plan de implementación en
> `docs/superpowers/plans/`— ya está en curso.

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
  ahora es serial (una cuenta a la vez); paralelismo/balanceo de carga y
  el resto de las mejoras (memoria de proyecto, economía de tokens,
  operación 24/7, modelo por rol, perfiles de tarea, hardening, entre
  otras) quedan documentados como trabajo futuro, no como parte de este
  spec.

## 1. Herramienta de control / UI — Vibe Kanban **[Aprobada]**

Evaluadas:

- **Vibe Kanban** — elegida, como backlog/kanban UI y punto de aprobación
  final, **no** como motor de orquestación multi-cuenta: Vibe Kanban corre
  como proceso local y lanza el CLI de Claude Code como **subproceso local**
  para sus propias funciones de ejecución de agente — no hace `docker exec`
  hacia contenedores por cuenta. La orquestación multi-contenedor/multi-cuenta
  descrita en las secciones 2, 4 y 5 (`docker exec -w ...` por cuenta, máquina
  de estados, handoff) es responsabilidad exclusiva del **Smart Dispatcher**
  construido en este proyecto, no una capacidad nativa de Vibe Kanban.
- **Integración Dispatcher ↔ Vibe Kanban:** Vibe Kanban expone un servidor
  **MCP local** que habla por `stdio` vía el subcomando `mcp` del propio
  binario (no por SSE como asumía `dispatcher/vibe_kanban_client.py`); no
  se expone por red — solo su UI web escucha, y únicamente en loopback.
  Sus herramientas usan
  vocabulario de **issue**, no de tarea
  (`list_issues`/`create_issue`/`get_issue`/`update_issue`/`delete_issue`,
  indexadas por `issue_id`), el servidor asigna un UUID propio a cada issue
  (`simple_id` es un candidato de clave legible aún sin confirmar), cada
  proyecto trae su propio conjunto fijo de estados (ambos a nivel de
  esquema; falta confirmarlos en vivo, filas V2.3–V2.4), y hay un muro de
  login en la nube (`api.vibekanban.com`) que puede bloquear el acceso. El
  Smart Dispatcher debe usar este servidor MCP con ese vocabulario para leer
  el backlog y actualizar el estado de los issues, en vez de leer
  directamente la base de datos interna (superficie no documentada y sujeta
  a cambiar entre versiones). Verificado contra `vibe-kanban@0.1.44`
  (`docs/ROADMAP.md`, filas V2.1–V2.6): transporte, herramientas y muro de
  login confirmados en vivo; IDs y estados (V2.3–V2.4) solo a nivel de
  esquema, y la ida y vuelta de la descripción (V2.5) todavía necesita una
  corrida en vivo.
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

> **Nota de revisión (2026-09-17):** el diseño original de esta sección
> (el volumen `claude_creds_<cuenta>` montado en
> `/root/.claude/credentials`, anidado dentro del volumen compartido
> `~/.claude`) falló en V0.4. El montaje anidado sí funcionaba —el volumen
> estaba montado, pero vacío—: el problema es que Claude Code nunca
> escribe en un subdirectorio `credentials/`. Guarda `.credentials.json`
> en la raíz de su *config home* (`~/.claude/.credentials.json`, es decir,
> en `claude_shared`, visible para las demás cuentas) y `.claude.json` en
> `~/.claude.json`, fuera de cualquier volumen, así que se perdía al
> recrear el contenedor. El texto que sigue describe el diseño corregido,
> ya implementado y verificado en V0.4 el 2026-09-19.

- Cada contenedor de agente tiene su propio *config home* de Claude Code,
  fijado en la imagen vía `ENV CLAUDE_CONFIG_DIR=/root/.claude-account`
  (`docker/agent/Dockerfile`) y respaldado por el volumen
  `claude_creds_<cuenta>` de esa cuenta. Ahí viven `.credentials.json`,
  `.claude.json` y todo lo demás que Claude Code guarda en su *config
  home*, así que una cuenta no puede leer las credenciales de otra. La
  única excepción conocida es `.device-keys.json`: el CLI tiene fija su
  ruta en `~/.claude/` (es decir, en `claude_shared`), con o sin
  `CLAUDE_CONFIG_DIR`, así que, si llega a crearse, lo comparten todas las
  cuentas. En la nueva ejecución de V0.4 (2026-09-19) no lo crearon ni el
  login, ni `/status`, ni recrear el contenedor.
- Lo que sí comparten todas las cuentas (histórico de sesiones, skills,
  agents, commands, plugins, `settings.json`) vive en el volumen
  `claude_shared`, montado en `/root/.claude`. El entrypoint de la imagen
  (`docker/agent/entrypoint.sh`) crea en cada arranque, dentro de
  `$CLAUDE_CONFIG_DIR`, un enlace simbólico hacia `/root/.claude/<nombre>`
  para cada nombre de esa lista explícita — nunca al revés, y nunca nada
  fuera de esa lista — así que el entrypoint nunca enlaza el login ni
  `.claude.json` hacia el volumen compartido.
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
   > path, y la autenticación OAuth vive en el config home separado y
   > propio de cada cuenta, resuelto en la sección 3), pero se recomienda
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
│  │ • ~/.claude compartido (sesiones+config); credencial propia de     │ │
│  │   cada cuenta en su propio config home, CLAUDE_CONFIG_DIR (sec. 3) │ │
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
  (mismo mecanismo de detección proactiva/reactiva de la sección 4a), en
  vez de por fin de fase.

Ninguna de las dos variantes se implementa como parte de este spec; se
deja como toggle futuro sobre la misma base (Smart Dispatcher,
`.hive/tasks/<task-id>.md`, sidecars DooD, observabilidad) para no tener
que rediseñar desde cero cuando se aborde.

Los bloques siguientes se agregaron el 2026-09-16, después de la
aprobación, y no cambian el alcance aprobado. Van de mayor a menor
prioridad; en ese orden, el workflow paralelo de arriba cae entre la
compactación a mitad de fase y los contenedores multiproveedor (ver la nota
de prioridad más abajo). Antes que todo esto van los huecos conocidos de la
implementación, que viven solo en `README.md` (*Future work*, *Known gaps*)
porque son defectos del código, no trabajo de diseño.

Y antes de los huecos conocidos van las verificaciones de los flujos reales,
con los contenedores levantados, que están en `docs/ROADMAP.md`. Los tests
unitarios simulan `docker exec`, el CLI de Claude Code y el MCP de Vibe
Kanban, así que ninguno de esos contratos está comprobado. El roadmap fija
el orden: etapa 0, verificar (V0–V5); etapa 1, corregir los huecos conocidos
con esos resultados y aceptar con una tarea de punta a punta; etapa 2, este
trabajo futuro, en el que cada bloque corre antes sus propias verificaciones
diferidas (D1–D6). Si una verificación falla, es un hallazgo: se registra y
se actualizan este spec y el README antes de diseñar el bloque que depende
de ella.

**Trabajo futuro — memoria de proyecto (fuera de alcance de este spec):**

Cada fase es un `claude -p` que arranca en frío: el rol es solo un nombre
en el prompt, redescubre el repo objetivo desde cero y le deja al rol
siguiente prosa libre. Queda documentado, sin diseñar en detalle, darles a
los agentes una memoria que viva en el propio proyecto:

- **Skills por rol (método, no convenciones):** un juego chico de skills
  sobre cómo trabajar para el Claude de cada contenedor agente
  (Arquitecto, Implementador, Revisor, Auditor), en vez de un prompt plano
  por rol. Cada skill es un `SKILL.md` corto más referencias que se abren
  bajo demanda. La fuente del método general es
  [superpowers](https://github.com/obra/superpowers), copiando skills
  sueltas y recortadas, no instalando el plugin en la imagen: su hook
  `SessionStart` inyecta `using-superpowers`, cuya regla de "invocar una
  skill si hay un 1% de chance de que aplique" gasta turnos en cada fase,
  y algunas skills esperan a un humano (`brainstorming` pide aprobar el
  diseño antes de escribir código, `finishing-a-development-branch`
  pregunta si mergear o abrir un PR), así que bajo `-p` la fase termina en
  una pregunta después de gastar la cuota. Vale copiar `writing-plans`
  (Arquitecto), `test-driven-development` y
  `verification-before-completion` (Implementador, Auditor),
  `receiving-code-review` (Implementador en rondas de revisión) y
  `systematic-debugging` (cualquier rol trabado más de un reintento). Se
  saltan `subagent-driven-development` y `using-git-worktrees`, porque el
  dispatcher ya reparte por rol y crea los worktrees. Las convenciones de
  un proyecto viven en sus docs, y si el proyecto trae sus propias
  `.claude/skills`, esas ganan en su dominio. Se entregan por llamada, no
  instaladas en el `/root/.claude` compartido (ver el bloque de perfiles):
  candidatos `--append-system-prompt-file`, `--agents` o un
  `--plugin-dir` por rol, el mismo flag que usan los packs. Incluye la
  regla **revivir antes de relanzar**: para retomar el trabajo de un subagente, primero intentar
  revivirlo por su ID (`SendMessage` al agent ID, que lo continúa con su
  contexto intacto), y solo si eso falla lanzar uno nuevo con el brief del
  handoff. Evidencia de otro setup multiagente sobre el mismo CLI: un
  subagente muerto por rate limit se revive solo por su `agent_id` crudo
  (no por el nombre con que se despachó) y solo mientras la sesión padre
  siga viva. *Sin verificar:* si un `claude -p --resume` de la sesión
  padre, posiblemente en otra cuenta, vuelve a hacerlos direccionables. Si
  no, el plan B es retomar la sesión previa del rol en la siguiente ronda.
  Material de partida para las skills de Revisor y Auditor: el plugin
  [thermos](https://github.com/cursor/plugins/tree/main/thermos) de Cursor
  (una revisión de correctitud y seguridad más otra de calidad de código,
  en paralelo y sintetizadas). Vale tomar: alcance limitado al diff,
  verificar un hallazgo antes de reportarlo, calibrar severidad, una lista
  de roturas de devex (variables de entorno, puertos, secretos), una vara
  de aprobación explícita y leer el diff antes que el resumen del
  Implementador. Hay que adaptarlo, no instalarlo: `thermo-nuclear-review`
  pasa a ser la skill del Revisor, que bloquea solo por correctitud,
  seguridad y regresiones claras (si las sugerencias de calidad bloquean,
  el loop de rondas acotadas no converge);
  `thermo-nuclear-code-quality-review` pasa a ser una pasada no bloqueante
  dentro de la fase del Auditor, con notas para un humano; y se descarta
  el orquestador `thermos`, porque su empaquetado supone Cursor, su paso
  de PR/BugBot no tiene PR que leer acá y dos revisores por ronda duplican
  la cuota. También hay que bajarle el tono en mayúsculas de "nada se
  puede escapar", que empuja a sobrerreportar.
- **Docs por proyecto, commiteadas con su código:** `decisions.md` (ADRs
  de negocio y de arquitectura, con estado cerrada / en pausa /
  reabierta), `learnings/` (fallos típicos), `debt/` (deuda declarada),
  `architecture.md`, `business.md` e `implementations/<task-id>.md`. Si
  el proyecto no tiene índice de docs, una fase de mapeo acotada (modelo
  más barato, presupuesto de turnos, opt-in porque gasta cuota) corre
  antes del Arquitecto y arma el mapa sin reescribir las docs que ya
  existan; después el mapa crece con cada tarea. El Arquitecto lee los
  índices y registra un ADR cuando la tarea decide algo, el Implementador
  declara deuda y propone aprendizajes, el Revisor trata como hallazgo un
  cambio de contrato sin su doc, y el Auditor es el **único escritor** de
  los índices. Las decisiones de negocio que un agente infiere del código
  quedan "sin confirmar" hasta que un humano las valide. Las fricciones
  con las skills mismas vuelven a ia-harness para revisión humana y nunca
  se instalan solas; las trampas del código van a `learnings/` del
  proyecto.
- **Índices y punteros:** cada índice tiene una columna de disparo
  ("cuándo aplica"), escrita como condición evaluable contra la tarea; el
  agente lee el índice completo y abre una entrada solo si su disparo
  coincide. `CLAUDE.md` es solo índice, nunca fuente. Se cita por ruta
  más un ancla estable (ID de ADR o de entrada, heading o nombre de
  símbolo), nunca por número de línea: una cita
  rancia que todavía resuelve apunta con confianza al lugar equivocado.
  Lo grande (diffs, logs, planes) viaja como ruta, y va inline solo bajo
  un umbral de bytes medido. Precedencia: docs > skills > `CLAUDE.md`; una
  contradicción entre capas es un bug de docs.
- **Handoff estructurado:** el cuerpo de `.hive/tasks/<task-id>.md`
  (mecanismo 1 de la sección 4a) pasa de prosa acumulada a un resumen
  corto por rol devuelto vía `--json-schema` (estado, qué cambió, qué se
  verificó, qué falta, riesgos, IDs de subagentes y qué hacía cada uno,
  aprendizajes y deuda propuestos, rutas al detalle y el veredicto del Revisor como campo en
  vez de una línea de texto), con tope de bytes por rol. Hoy el recorte es
  ciego: `_truncate_for_handoff` guarda los primeros 500 y los últimos
  1.500 caracteres, y el medio de un retorno largo, justo donde suelen ir
  los hallazgos, se pierde. El tope lo impone el propio dispatcher: a un
  retorno que se pasa le hace un `--resume` pidiendo uno más corto, acepta
  el reintento tal cual y loguea el exceso para ajustar los topes con
  datos. El detalle va a archivos separados por vida útil: lo durable
  (ADRs, aprendizajes, `implementations/<task-id>.md`) en la rama de la
  tarea, y lo efímero de cada ronda (hallazgos de revisión, logs de tests)
  en `.hive/tasks/<task-id>/`; `.hive` guarda el resumen y las rutas. Lo
  que queda en disco sobrevive solo si se commitea o vive fuera del
  worktree. Los IDs de subagentes registrados son los que permiten que una
  fase retomada o posterior intente revivirlos.
- **Docs y tests al día, exigidos fuera del modelo:** hoy nada lo exige.
  `_role_prompt` solo manda rol, tarea y archivo; el dispatcher no corre
  tests ni mira el diff, y el único hook (`hooks/emit_event.py`) es de
  observabilidad. Los deberes de arriba (TDD, la regla de contratos del
  Revisor, los índices del Auditor) son instrucciones: se cumplen mientras
  un modelo las siga y otro note cuando no. Tres capas, de la más estricta
  a la más flexible:
  - *Controles del dispatcher entre Implementador y Revisor:* deterministas,
    sin cuota y fuera del alcance del agente.
    - Tests en el diff: si `git diff --name-only` muestra código cambiado
      sin tests, la tarea no llega al Revisor. El Implementador recibe un
      `--resume` que pide tests o una justificación explícita, y el
      Revisor juzga esa justificación.
    - Correr los tests: el comando de tests del proyecto (registrado por
      la fase de mapeo) corre con `docker exec` sin `claude`. Si falla,
      arranca otra ronda con la ruta al log, sin gastar una llamada al
      Revisor. Así también se verifica, en vez de creerle, que el
      Implementador diga que pasan. Pendiente: un worktree nuevo no tiene
      `node_modules`, así que falta decidir si se instalan dependencias o
      se reutiliza una caché.
    - Contratos sin docs: un cambio en OpenAPI, `.env.example`,
      migraciones, exports públicos o flags de CLI sin cambio de docs es
      un hallazgo que el Revisor tiene que responder.
    - Punteros rotos: las docs citan por ruta más un ancla estable (ver
      índices y punteros), así que un grep dice si lo citado todavía existe. Si no, pasa a ser tarea del
      Auditor.
  - *Hooks de Claude Code en el contenedor*, solo como feedback dentro de
    la sesión: un `PostToolUse` sobre Edit/Write que corra el formatter o
    el typecheck del archivo tocado es barato y avisa temprano. Se
    desaconseja un hook `Stop` que impida terminar sin tests: bajo `-p`
    cada rechazo es otro turno pagado, necesita revisar `stop_hook_active`
    para no entrar en loop y solo ve su propia sesión, mientras que el
    control del dispatcher ve el diff completo sin gastar cuota.
  - *Instrucciones por rol:* los criterios de aceptación del Arquitecto
    nombran qué tests prueban la tarea y qué docs cambian, para que el
    Revisor tenga algo concreto contra qué comparar.

  Riesgos: "nada de código sin tests" se cumple con tests triviales; por
  eso la regla es "tests o justificación", los tests tienen que pasar y el
  Revisor sigue juzgando su calidad. Y el control de docs se limita a
  contratos, porque exigir docs en cualquier cambio acumula docs que nadie
  lee.
- **Deuda declarada, reflejada en Vibe Kanban:** el índice `debt/` sigue
  siendo la fuente de verdad. Los agentes lo leen filtrando por su columna
  "dónde", está versionado con el código y no depende de Vibe Kanban, que
  en este diseño es una ayuda visual cuyo MCP todavía no coincide con el
  cliente del dispatcher (ver la integración Dispatcher ↔ Vibe Kanban más
  arriba). El flujo:
  1. El Implementador declara la deuda en su retorno estructurado: si es
     introducida o encontrada, qué es, por qué queda así, cuánto cuesta no
     arreglarla y qué la resolvería. La deuda encontrada cuenta solo en
     archivos que la tarea tocó y que no estén ya en el índice. Declarar
     deuda nunca reemplaza bloquear: lo que bloquearía la tarea (p. ej.
     una decisión que la tarea no especifica o un cambio de esquema) la
     sigue bloqueando.
  2. El Revisor da un veredicto por declaración. La aceptada sigue. La
     rechazada pasa a ser un hallazgo a corregir en la ronda siguiente y,
     como cualquier hallazgo, cuenta para `max_revision_rounds`, tras lo
     cual la tarea queda bloqueada. Un bloqueo disfrazado deja la tarea
     bloqueada.
  3. El Auditor, como único escritor, agrega las aceptadas a `debt/`. Si
     un spec posterior decide el arreglo, la entrada apunta a esa sección.
  4. El dispatcher, no un agente, crea una tarjeta por entrada aceptada con
     `VibeKanbanClient.create_task`, que existe pero nadie llama todavía.
     La tarjeta lleva la etiqueta `debt`, queda en backlog y nunca se
     despacha sola. La entrada y la tarjeta guardan cada una el ID de la
     otra. Si los agentes crearan tarjetas, se duplicarían en cada ronda,
     y un agente que puede crear tareas puede asignarse trabajo.
  5. Que un humano saque la tarjeta del backlog aprueba el trabajo. La
     tarea que resuelve la deuda lo dice en su retorno, y al mergear el
     dispatcher marca la entrada como resuelta y cierra la tarjeta.

  Para no inundar el tablero, solo la deuda aceptada tiene tarjeta, y solo
  después de buscar duplicados en el índice.
- **Aprendizajes compartidos que sobreviven a una fase muerta:** el índice
  `learnings/` ya da una lectura en dos niveles: el agente lee el índice
  completo y abre una entrada solo si su "cuándo aplica" coincide con su
  tarea. Lo que falta es cómo le llega un aprendizaje a otros agentes.
  Cada fase es un `claude -p` aislado y la entrada viaja en la rama de la
  tarea, así que una tarea concurrente no la ve hasta el merge y una tarea
  rechazada la pierde. Una fase que muere por rate limit o timeout, o una
  tarea que termina bloqueada, ni siquiera llega al Auditor, y justo esas
  paredes son las que más vale registrar. Por eso:
  - *Buzón:* cualquier fase puede agregar una entrada en cualquier momento,
    incluso justo antes de morir, en `.hive/learnings/inbox/`. Ese
    directorio está fuera del worktree y ya montado en los dos
    contenedores agente. Cada entrada es su propio archivo, así que dos
    fases concurrentes nunca editan el mismo. Los agentes buscan con grep
    en el buzón además del índice, así que una entrada llega a otras
    tareas de inmediato, sin esperar un merge.
  - *Único escritor:* el Auditor promueve las entradas del buzón de su
    tarea al índice, en la rama de la tarea; salen del buzón cuando esa
    rama se mergea. Si la tarea falla, termina bloqueada o su rama se
    descarta, el dispatcher, sin LLM, marca sus entradas "sin confirmar" y
    las deja en el buzón, y la siguiente tarea de ese proyecto que llegue
    al Auditor las lleva a su rama.
  - *Dos alcances:* las trampas del proyecto van a su `learnings/` y se
    commitean con el merge. Las trampas del entorno, del harness o del CLI
    (p. ej. "un worktree nuevo no trae `node_modules`") van a un almacén de
    ia-harness compartido entre proyectos. Igual que las fricciones con las
    skills, un humano las revisa antes de que las vean agentes de otros
    proyectos.
  - *Formato:* el índice tiene las columnas `# | Aprendizaje | Cuándo aplica
    | Estado`. Una entrada tiene cuándo aplica, dónde se descubrió (tarea y
    fase), el síntoma con el error exacto, por qué pasa y la regla. Como el
    error queda textual, las skills por rol pueden pedir "antes de depurar,
    busca con grep el mensaje exacto en los aprendizajes y en el buzón", y
    así un agente
    que choca con una pared encuentra la entrada aunque su tarea no
    pareciera relacionada. Cuando el índice crezca, el dispatcher le pasa a
    cada fase solo las filas que coinciden con el perfil, las etiquetas o
    las rutas que nombra el plan de la tarea.
  - *Contra el envenenamiento:* un agente que entendió mal algo convertiría
    ese error en regla para todos. Una entrada queda "sin confirmar" hasta
    que otra tarea choque con la misma pared o un humano la confirme; los
    agentes igual leen las entradas sin confirmar, marcadas como tales.
    Toda entrada necesita evidencia (el error y el comando), y la que tiene
    punteros que ya no resuelven queda marcada (control de punteros rotos,
    más arriba).

  Una trampa es "no pises esto"; un código a medio hacer es deuda, no un
  aprendizaje.

Depende de que los agentes reciban la descripción de la tarea y de que el
trabajo de cada rol se commitee; hoy no pasa ninguna de las dos cosas (ver
*Known gaps* en `README.md`). Las tarjetas de deuda dependen además de que
el MCP de Vibe Kanban permita crear tareas, algo sin verificar. Sin
diseñar: el esquema del handoff, dónde viven los archivos de skills
(horneados en `docker/agent/` al construir la imagen vs. montados junto a
`.hive`), cómo se le indica a un rol qué skill aplica (para las que
dependen del tipo de tarea y no del rol, ver el bloque de perfiles), el
formato de las entradas del buzón y dónde vive el almacén de aprendizajes
compartido entre proyectos.

**Trabajo futuro — economía de tokens (fuera de alcance de este spec):**

Con 2 cuentas Pro la cuota es el cuello de botella. Otro setup multiagente
sobre el mismo CLI midió sus transcripts: releer contexto fue el 74% del
costo de sus subagentes y toda la prosa que escribieron, el 1,3%; un agente
carga 23–35K tokens fijos antes de hacer nada; el costo siguió las vueltas
de verificación e iteración, no el tamaño de la tarea; y la intuición sobre
dónde se iban los tokens falló las tres veces que se contrastó con datos.
Queda documentado, sin diseñar en detalle:

- **Medir por fase antes de optimizar:** guardar `usage`, `num_turns`,
  `total_cost_usd` y `duration_ms` de cada `claude -p` (hoy se descartan)
  con rol, modelo, effort, ronda y una huella de la config del agente
  (versión del CLI, skills, servidores MCP, ventana de compactación), y
  comparar tasas entre regímenes de config, no totales. La selección de
  modelo por rol depende de estos datos.
- **Cuota como subproducto:** el CLI (revisado en 2.1.273) define un
  evento `rate_limit_event` con `utilization`, `resetsAt` y
  `rateLimitType`. Si `--output-format stream-json --verbose` lo emite en
  cuentas Pro (*sin verificar*), reemplaza el sondeo proactivo de `/usage`
  de la sección 4a: ese sondeo no cobra turno, pero arranca el CLI entero
  por cuenta y parsea texto libre frágil, mientras que el evento llega con
  cada fase, estructurado y con la hora exacta de reset para reintentar
  las tareas bloqueadas por cuota.
- **Contexto fijo mínimo:** solo los plugins y servidores MCP que el rol
  usa, `CLAUDE.md` como índice y skills bajo demanda; el `usage` del
  primer turno mide el resultado.
- **Menos turnos:** en las skills, agrupar llamadas independientes en una
  respuesta, leer archivos por rango, recortar salidas largas y pasar
  artefactos como rutas. El Arquitecto fija la verificación más barata que
  pruebe cada paso.
- **Tope a lo que devuelve cada rol, en dos niveles:** todo lo que queda
  en el handoff se relee en cada fase siguiente. Lo que un rol le devuelve
  al dispatcher lo lee Python, que no cuesta tokens, pero termina en el
  handoff: lo acotan el schema y el tope del dispatcher del bloque
  anterior. Lo que los subagentes propios de un rol le devuelven a su
  sesión lo relee un LLM en cada turno que le queda: para eso la
  configuración de la imagen del agente instala un hook `SubagentStop`
  que rechaza una vez el retorno largo (pidiendo el detalle a disco y un
  resumen corto) y deja pasar el reintento. Así ese otro setup mantiene
  sus retornos en 3–4 KB; antes del hook midió un retorno de 14.030
  caracteres donde el contrato pedía siete líneas, así que una regla en el
  prompt sola no alcanza. Su hook reconoce el rol por un comentario
  centinela en el prompt del subagente y no por `agent_type`, que llegó
  poblado solo en el ~7% de los cierres, y loguea cada exceso. Los
  formatos de línea compactos para esos retornos se toman de `cavecrew`
  (ver el punto de caveman, más abajo).
- **Retomar vs. reiniciar:** `--resume` (mecanismo 2 de la sección 4a)
  relee el transcript entero, y tras un cooldown o en otra cuenta el caché
  de prompt casi seguro está frío. Conviene si la fase ya había avanzado;
  si apenas empezó, sale más barato un cold-start desde el handoff. El
  umbral lo fijan las mediciones.
- **Ventana de auto-compact:** bajar `autoCompactWindow` en la
  configuración de la imagen del agente acota lo que una fase larga relee
  por turno (ese setup simuló 120K como ~12% más barato que 150K).
- **[caveman](https://github.com/JuliusBrussee/caveman), pieza por
  pieza:** junta varios ahorradores de tokens con evidencia muy distinta
  detrás. Evaluado para el Claude de los contenedores agente:
  - *Skill de estilo de salida (MIT):* no por defecto. Hace que el agente
    escriba prosa telegráfica, pero en una fase agéntica la mayoría de los
    tokens es releer contexto, código y llamadas a herramientas que la
    skill no toca. El único A/B de terceros sobre tareas reales de Claude
    Code (JetBrains, 86 tareas) midió 8,5% menos tokens de salida, cerca
    de 10% del costo, sin cambio de calidad, mientras que sus ~1K tokens de
    reglas se releen en cada turno. Su propio `docs/HONEST-NUMBERS.md`
    lista casos netos negativos y pide medirla con los totales del
    proveedor. Sus límites (código, commits, docs y PRs en prosa normal;
    claridad completa en advertencias de seguridad y acciones
    irreversibles) no chocan con las docs por proyecto. Queda como A/B
    detrás de un flag cuando existan las mediciones por fase, acotada a lo
    que las fases siguientes releen (retornos y campos del handoff), que
    el schema y los topes ya acotan de forma determinista.
  - *`cavecrew` (MIT):* tomar sus contratos de retorno, no sus agentes. El
    investigador devuelve líneas `path:line — símbolo — nota`; el
    constructor, `path:line-range — cambio` más `verified:` o un rechazo de
    una palabra (`too-big.`, `needs-confirm.`, `ambiguous.`,
    `regressed.`); y el revisor, `path:line: severidad: problema. fix.` más
    totales, o `No issues.`. Encajan en los retornos de subagentes y en el
    archivo de hallazgos del Revisor. Los agentes traen política propia (el
    revisor fijo en haiku, el constructor rechaza cambios de más de dos
    archivos) que deben decidir las skills por rol y la selección de
    modelo por rol.
  - *`caveman-compress` (MIT):* no. Gasta llamadas a Claude en reescribir
    en el lugar `CLAUDE.md` y archivos de memoria, guarda el respaldo fuera
    del repo (se pierde con el contenedor), y su ~46% menos de entrada
    sobre cinco fixtures no viene con ninguna afirmación de calidad
    equivalente. Las docs por proyecto se commitean en el repo objetivo y
    también las leen humanos; `CLAUDE.md` como índice más entradas que se
    abren por disparo ataca el mismo costo sin una reescritura con
    pérdida.
  - *Proxy y motor de compresión (`caveman wrap claude`; CLI MIT, runtime
    BSL-1.1):* la única pieza que ataca la relectura, y la de mejores
    números. Un benchmark fijado sobre Claude Code con salidas de
    herramientas de 60–95 KB (logs, salida de tests, JSON, CSV, YAML)
    midió 33,2% menos tokens de entrada reportados por el proveedor, con
    18 de 18 respuestas correctas (IC 95%: 14,6–48,5%), lo que calza con
    Implementador y Auditor corriendo suites y leyendo logs. La letra
    chica: fixtures controlados, no producción, y HTML empeoró 9,9%; la
    compresión es con pérdida (los originales quedan en un almacén local
    con un handle de recuperación, pero un agente igual puede actuar
    sobre un log recortado); según su doc de despliegue, un proxy
    compartido autenticado por token no funciona con logins Claude
    Pro/Max, así que correría como wrap local dentro de cada contenedor
    agente (un CLI de Node.js 22 más un binario Go en la imagen, y un
    salto más entre la cuenta y Anthropic); y BSL-1.1 permite uso propio
    autoalojado, producción incluida, pero ofrecer ia-harness a terceros
    como servicio alojado pediría licencia comercial. Es el mejor
    candidato del conjunto, como experimento en la imagen de una cuenta y
    con A/B sobre las mediciones por fase antes de adoptarlo. *Sin
    verificar:* que `-p --output-format json`, sus cifras de `usage` y
    `--resume` se comporten igual a través del wrap.

No aplican acá la disciplina de compactar un hilo principal de larga vida
(ninguna sesión vive más que su fase), ni la conclusión de que el reparto
Opus/Sonnet casi no mueve el costo, que se midió con precio por token y no
con los límites del plan Pro.

**Trabajo futuro — operación 24/7 desatendida (fuera de alcance de este spec):**

El Resumen y el diagrama de la sección 5 describen una plataforma
persistente 24/7, pero el dispatcher implementado es un CLI de una sola
pasada (`run-task` para una tarea), así que correr sin pausa todavía depende
de automatización externa. Queda documentado, sin diseñar en detalle, lo
que haría falta para que aguante sin supervisión:

- **Loop de larga vida:** toma las tareas listas respetando `depends_on`,
  que hoy se guarda en el frontmatter pero nunca se chequea.
- **Distinguir bloqueo por cuota de fallo:** hoy una tarea que no encuentra
  cuenta disponible queda `blocked` hasta que intervenga un humano. Las
  bloqueadas por cuota podrían reintentarse solas: a la hora `resetsAt` del
  `rate_limit_event` (bloque de economía de tokens) si el CLI lo emite; si
  no, a las horas de reset del texto de `/usage` cuando se puedan parsear;
  y si no, con un rechequeo periódico.
- **Ejecuciones superpuestas:** un guard de instancia única (p. ej. `flock`
  sobre `state_dir`) para que dos corridas no tomen la misma cuenta.
  `acquire_lock` ya rechaza un lock vivo sobre la tarea, pero dos corridas
  de tareas distintas todavía compiten por los archivos de estado de las
  cuentas.
- **Cuentas `BUSY` huérfanas:** un SIGKILL, un OOM o un reinicio del host
  dejan la cuenta `BUSY` en disco, y el `except` de `dispatch_phase` no
  alcanza a capturarlo. Un Ctrl+C también la deja `BUSY`, a propósito:
  matar el `docker exec` del host no detiene el `claude` dentro del
  contenedor, que sigue corriendo hasta `phase_timeout_seconds`. Hace falta
  un recolector; una regla candidata es liberar una cuenta `BUSY` cuyo
  archivo de estado sea más viejo que `phase_timeout_seconds` más los 30
  segundos de gracia del kill.
- **Fase huérfana tras Ctrl+C:** el heartbeat muere con el proceso del
  host, así que el lock de la tarea expira a los `heartbeat_ttl_seconds`
  (120 por defecto) mientras ese `claude` huérfano puede seguir hasta
  `phase_timeout_seconds` (7200). Una re-ejecución en esa ventana toma la
  tarea en otra cuenta y puede trabajar el mismo worktree a la vez, porque
  los checkouts de `projects_root` se comparten entre contenedores. El
  recolector de arriba podría sostener el lock (o saltarse la tarea)
  mientras alguna cuenta siga `BUSY` con ese task ID.
- **Estado de Kanban pisado:** una segunda corrida que choca con un lock
  vivo marca la tarea `blocked` en Vibe Kanban y pisa el
  `in_progress:<rol>` bajo el que la primera sigue trabajando. Conviene
  chequear el lock antes de escribir `in_progress`, o reportar un estado
  distinto.
- **Timeout por llamada a Vibe Kanban:** `VibeKanbanClient._call_async` no
  pasa `read_timeout_seconds` a `ClientSession` ni a `call_tool`, así que
  un servidor que acepta la conexión y nunca responde deja colgada una
  actualización de estado que debía ser best-effort.
- **Configuración de logging:** `dispatcher/cli.py` no configura ningún
  handler, así que los warnings del dispatcher (actualización de Kanban
  fallida, lock de otro dueño, `/usage` imparseable) llegan a stderr solo
  por el handler de último recurso de `logging`, sin timestamps ni
  contexto.
- **Prompt corto al retomar:** tras un rate limit, un "continúa donde
  quedaste" en vez de reenviar el prompt completo del rol.

**Trabajo futuro — modelo por rol y escalado de effort (fuera de alcance de este spec):**

Correr opus en los cuatro roles es lo que más rápido gasta la cuota. Hoy
`default_model` (`config.example.yaml`) aplica el mismo modelo a todos los
roles, y el effort solo escala cuando el loop Implementador/Revisor supera
`escalate_effort_after_round`. Queda documentado, sin diseñar en detalle:

- **Modelo por rol:** p. ej. opus para Arquitecto y Auditor (planificación
  y juicio) y sonnet para Implementador (ejecución), quizá también para las
  primeras rondas del Revisor. El reparto se decide primero con las
  mediciones por fase del bloque de economía de tokens, que muestran qué
  roles consumen de verdad la cuota.
- **Escalar por otras señales además de la ronda:** subir el effort (o
  cambiar de modelo) si el Revisor repite la misma objeción, o si un rol
  devuelve un resultado sospechosamente corto.

Sin diseñar: el schema de config (`default_model` como fallback vs. un mapa
`models: {arquitecto: opus, ...}`), y cómo detectar "la misma objeción" o
"sospechosamente corto" en un `result_text` libre sin armar una heurística
que nunca se dispare como se esperaba.

**Trabajo futuro — perfiles de tarea y roles nuevos (fuera de alcance de este spec):**

Las skills por rol (bloque de memoria de proyecto) dicen cómo trabaja un
rol, no de qué trata la tarea. Una tarea de frontend gana con skills de
diseño y de verificación en navegador que una de backend pagaría sin usar:
la descripción de cada skill instalada está en contexto en cada turno, y
el `skills:` de un subagente toma de las mismas skills instaladas, así que
esconder un pack detrás de un subagente no lo saca del contexto del padre.
Queda documentado, sin diseñar en detalle:

- **Mecanismo:** la tarea lleva un perfil (`web-frontend`, `e2e`, `3d`,
  …), puesto por un humano como etiqueta en Vibe Kanban o, si falta,
  elegido por el Arquitecto desde un catálogo corto de nombres con una
  línea de descripción (nunca las skills). El dispatcher lo guarda en
  `.hive/tasks/<task-id>.md` y agrega `--plugin-dir
  /opt/packs/<perfil>/<rol>` al `claude -p` de esa fase, en el mismo
  contenedor de la cuenta (el flag se repite, así que los packs se
  apilan). Ninguna sesión orquestadora necesita saber que existen. El flag
  se vuelve a pasar en cada `--resume`, incluido el traspaso por cuota:
  *sin verificar* si una sesión retomada conserva los plugins de la
  llamada original. Una segunda imagen (`agent-web`) solo se justifica
  por dependencias pesadas; la imagen actual (`node:20-slim`) no trae
  Chromium.
- **Perfil por defecto del proyecto:** la mayoría de las tareas de un
  proyecto quieren los mismos packs, así que el proyecto lleva perfiles
  por defecto y la etiqueta de la tarea les suma: una fase recibe los
  perfiles del proyecto más los de la tarea.
  - *Activar, nunca desactivar:* `/root/.claude` es el volumen
    `claude_shared`, compartido por las dos cuentas y todos los proyectos,
    así que apagar ahí una skill para un proyecto la apaga para todos. El
    CLI 2.1.273 tampoco permite apagar una sola skill por llamada
    (`--disable-slash-commands` las apaga todas). Por eso, nada propio de
    un proyecto en el volumen compartido: una base mínima con las skills
    por rol, siempre activa para su rol y entregada por llamada igual que
    los packs (bloque de memoria), más los packs que se agregan por llamada
    con `--plugin-dir`.
  - *La señal es un archivo de ia-harness commiteado en el proyecto* (p. ej.
    `.ia-harness.yaml` con `profiles: [web-frontend, e2e]` y la evidencia
    de cada uno), no `CLAUDE.md` ni `.claude/`: muchos proyectos ya los
    traen, escritos para humanos, y no dicen nada de packs.
  - *Si el archivo falta, la detección lee manifiestos sin gastar cuota:*
    react, vue, svelte o next en `package.json` → `web-frontend`;
    `playwright.config.*` o cypress → `e2e`; `three` → `3d`; Dockerfile o
    terraform → `infra`; prisma o `migrations/` → `migrations`. Un LLM
    decide solo los casos ambiguos (p. ej. un monorepo), dentro de la fase
    de mapeo, y un humano aprueba el resultado en Vibe Kanban.
  - El archivo guarda un hash de los manifiestos que leyó. Si cambian las
    dependencias, la detección se repite y el cambio se le propone a un
    humano; nunca se aplica solo.
  - *Ante la duda, el pack queda apagado:* uno que falta se nota en la
    revisión de esa tarea; uno que sobra gasta tokens en cada turno sin que
    nadie lo vea.
- **Candidatos para `web-frontend`, evaluados:**
  - [playwright-skill](https://github.com/willmarple/playwright-skill):
    sí, y el más valioso, porque cierra el ciclo que los demás dejan
    abierto: el agente renderiza, saca una captura (`playwright-cli
    screenshot`) y lee el PNG, en vez de juzgar la UI por su código.
    Necesita Chromium y `@playwright/cli`.
  - [impeccable](https://github.com/pbakaus/impeccable): sí, adaptado. Sus
    comandos calzan con los roles: `shape` para el Arquitecto, su flujo de
    construcción por defecto para el Implementador, `audit` (a11y,
    rendimiento, responsive) para el Revisor, `critique` y `polish` para
    el Auditor. Para headless hay que quitar las paradas que esperan
    AskUserQuestion (`init`, `document`, `extract`, `quieter`, `overdrive`
    y la pregunta final de `critique`) y los hooks `PostToolUse`
    (Edit|Write) y `Stop` del plugin.
  - [ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill):
    solo la skill `ui-ux-pro-max`, cuyo `search.py` consulta bajo demanda
    una base local de estilos, paletas y tipografías (para el Arquitecto).
    El resto no: `design`, `banner-design`, `brand` y `slides` generan
    imágenes con APIs externas que piden sus propias keys, y `ui-styling`
    se solapa con impeccable.
  - [taste-skill](https://github.com/leonxlnx/taste-skill): mayormente no.
    Su `SKILL.md` principal pesa ~87 KB (~22K tokens) cada vez que se
    dispara, y su regla de salida completa choca con los topes de retorno
    por rol del bloque de economía de tokens. A lo sumo, tomar algunos de
    sus antipatrones para el pack de impeccable.
  - [awesome-design-skills](https://github.com/bergside/awesome-design-skills):
    no va en la imagen. Cada entrada es un estilo visual; el proyecto
    trae a su repo el que eligió (`npx typeui.sh pull <estilo>`) como parte
    de sus docs de diseño.
  - [img2threejs](https://github.com/img2threejs/img2threejs) (reconstruye
    un objeto de una imagen de referencia como modelo procedural en
    Three.js): solo en un perfil `3d` opt-in, para tareas que construyen
    esas escenas.
- **Reglas de los packs:** una sola dirección de diseño por pack; dos
  skills de estilo tiran para lados distintos y el Revisor no sabe cuál
  debía seguir el diff. El estilo mismo (tokens, componentes, tono) vive
  en `DESIGN.md` y `PRODUCT.md` del proyecto objetivo, que ganan sobre
  cualquier pack, igual que las docs ganan sobre las skills por rol. Cada
  pack se vendoriza, se fija la versión y se recorta para headless (sin
  preguntas a un humano, sin hooks innecesarios), y un perfil se queda
  solo después de comparar algunas tareas reales con y sin él usando las
  mediciones por fase.
- **¿Un rol Diseñador? No como fase.** Una fase fija de diseño suma un
  arranque en frío y un handoff a cada tarea con cuota escasa, y diseña
  antes de que haya nada renderizado. La decisión de diseño que más pide
  criterio, la dirección visual, es de un humano; las propias paradas de
  impeccable caen justo en los comandos que la fijan. En su lugar:
  - Una **tarea de bootstrap del sistema de diseño**, única y opt-in por
    proyecto, como la fase de mapeo: escribe `PRODUCT.md` y `DESIGN.md`
    (desde la UI existente en un proyecto viejo, desde el brief en uno
    nuevo), marca como "sin confirmar" lo inferido y un humano la aprueba
    en Vibe Kanban (paso 6 de la sección 5). Las tareas de UI hacen
    `depends_on` de esa aprobación, no solo de que el pipeline termine.
  - El **perfil `web-frontend` sobre los roles existentes:** el Arquitecto
    especifica la UI (estados vacío, cargando y error; breakpoints; qué
    tokens y componentes de `DESIGN.md`), el Implementador la construye y
    revisa sus propias capturas, el Revisor compara capturas contra
    `DESIGN.md` y bloquea solo por fallos de accesibilidad (WCAG), layout
    roto o regresiones, y el Auditor deja el pulido de diseño como notas
    no bloqueantes para un humano.

  Se reevalúa una fase de Diseñador solo si las mediciones por fase
  muestran tareas de UI rebotando entre Implementador y Revisor por
  hallazgos de diseño.
- **Otros roles:** un rol nuevo tiene que aportar algo que un perfil no
  puede: un agente independiente que verifique el trabajo, un artefacto
  con vida propia o una puerta humana. Con esa vara:
  - *Mapeador:* ya es la fase de mapeo; formalizarla con skill propia y
    modelo más barato (bloque de modelo por rol).
  - *Tester/QA:* sin fase aparte. El Arquitecto escribe los criterios de
    aceptación y el Auditor corre las pruebas e2e con un perfil `e2e`.
    Que otro agente escriba los tests antes que el código (tests
    adversariales) vale como experimento, no como default.
  - *Revisor de seguridad:* un pack del Revisor (la revisión de
    correctitud y seguridad de thermos más Semgrep, bloque de inspección
    de código), activado en tareas que tocan auth, pagos o secretos.
  - *Documentador:* no; rompería la regla del Auditor como único escritor
    de los índices.
  - *Integrador:* commitear, rebasear y abrir el PR es código determinista
    del dispatcher (ver *Known gaps* en `README.md`). Una fase LLM solo
    paga su cuota resolviendo conflictos de rebase, que crecen con el
    workflow paralelo.
  - *Descomposición de épicas:* un modo del Arquitecto que propone tareas
    hijas, cada una con `depends_on` y perfil, para que un humano las
    apruebe. *Sin verificar:* si el MCP de Vibe Kanban permite crear
    tareas.
  - *Bugfix* (reproducir antes de arreglar), *infra/DevOps*, *rendimiento*
    y *migraciones de datos* (puerta humana antes de cualquier cosa
    irreversible): perfiles o tipos de tarea, no roles.

Depende de las skills por rol y de los mismos *Known gaps*. *Sin
verificar:* si un dev server más Chromium headless caben en el `mem_limit`
de 4 GB del contenedor agente (`docker/compose/docker-compose.agents.yml`),
o si el navegador puede correr en el sidecar dind de la cuenta (p. ej. la
imagen de Playwright) y aun así llegar al dev server. Sin diseñar: el
catálogo de perfiles, el esquema de `.ia-harness.yaml`, la convención de
etiquetas en Vibe Kanban y dónde viven los packs (horneados en
`/opt/packs` vs. montados).

**Trabajo futuro — observabilidad y hardening (fuera de alcance de este spec):**

Aceptable para un solo operador en loopback más Tailscale, pero conviene
endurecerlo, porque los contenedores agente corren código arbitrario de
repos clonados en la misma red:

- **Colector sin autenticación:** `POST`/`GET /events` no piden
  credenciales, así que cualquier cosa en `ia_harness_net`, agentes
  incluidos, puede leer o falsificar eventos. Un token compartido lo
  cierra.
- **Secretos en los eventos:** los payloads de los hooks incluyen entradas y
  salidas de herramientas (contenido de archivos, archivos de entorno,
  tokens) y llegan tal cual a SQLite. Hay que redactarlos en
  `hooks/emit_event.py`.
- **Autenticación del dashboard (sección 6):** ya compara en tiempo
  constante (`hmac.compare_digest`), pero lo guardado sigue siendo un
  SHA-256 sin salt de la contraseña. Un digest con salt (p. ej.
  `salt$sha256(salt + password)`) mantiene `scripts/configure.sh` sin
  Python; ojo con que compose interpola `$` en los valores de `.env`.
  Colector y dashboard corren además el servidor de desarrollo de Flask,
  no un servidor WSGI de producción.
- **Vista por tarea:** el dashboard muestra solo los últimos 200 eventos
  crudos; una vista por tarea (fase, cuenta, duración, rondas, veredicto,
  costo) respondería directo "qué le pasó a esta tarea".
- **Socket de Docker del dispatcher:** el servicio `dispatcher` monta el
  `/var/run/docker.sock` del host, que equivale a root en el host. Un proxy
  de socket limitado a `exec` lo acotaría. No contradice la sección 4b: ahí
  se descartó el proxy para aislar a los agentes, que tienen su propio
  daemon en el sidecar; el dispatcher sí necesita el daemon del host para
  hacer `docker exec` en los contenedores agente.

**Trabajo futuro — inspección de código en los contenedores agente (fuera de alcance de este spec):**

Un servidor MCP de grafo de conocimiento (p. ej. uno parseado con
tree-sitter que responde llamadores, llamados e impacto) le da a un agente
respuestas estructurales ("qué llama a esto", "qué se rompería") mucho más
baratas que grep. Hoy `docker/agent/` no trae nada de eso: cada rol trabaja
con lecturas de archivos y comandos de shell. Conviene empezar por análisis
estático para Revisor y Auditor, que es determinista y barato; los
servidores de navegación rinden sobre todo en repos grandes, y las
definiciones de herramientas de cada servidor MCP ocupan contexto en cada
turno, que en Claude Pro es cuota (bloque de economía de tokens). Opciones
reales, ninguna evaluada todavía en este repo:

- **Navegación semántica vía MCP**, para Arquitecto e Implementador:
  [CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext)
  (tree-sitter, CLI + MCP, base de datos de grafos) o
  [Serena](https://github.com/oraios/serena) (recuperación y edición a
  nivel de símbolo sobre LSP, más de 20 lenguajes).
- **Análisis estático y de seguridad por patrones**, para Revisor y
  Auditor: [Semgrep MCP](https://mcp.directory/blog/semgrep-mcp-complete-guide-2026),
  una pasada sistemática de seguridad y calidad en vez de depender de la
  lectura del diff que haga el propio modelo.
- **Linters propios del proyecto objetivo**, que las skills por rol
  mandan a correr. Para proyectos objetivo en TS/JS,
  [anti-slop](https://github.com/dmmulroy/anti-slop) es un set listo:
  reglas de Oxlint contra patrones de poca evidencia que los agentes
  suelen escribir (`unknown` sin validar, aserciones de tipo encadenadas,
  mocks de módulos). Se vendoriza una vez en el repo objetivo con su skill
  `install-anti-slop`, que copia las reglas a `tools/oxlint/anti-slop/`,
  fija `oxlint` y `@oxlint/plugins`, fusiona `oxlint.config.ts` y activa
  todas las reglas genéricas como `error` (las de Effect solo si el repo
  usa Effect). Desde ahí es determinista y no cuesta tokens por corrida,
  así que el Revisor deja de gastar turnos en esos patrones. No es
  automático: cambia las dependencias y la política de lint del proyecto,
  codifica el gusto de un autor (`no-module-mocking` prohíbe
  `vi.mock`/`jest.mock`), y en un repo existente todo en `error` inunda el
  lint con violaciones ajenas a la tarea, así que el Implementador quema
  cuota arreglando código viejo o el Revisor bloquea por eso. La skill
  puede ir en la imagen del agente (solo carga su descripción), pero corre
  solo cuando una tarea humana lo pide; encaja mejor en proyectos TS
  nuevos, con el Arquitecto proponiéndolo como ADR. Una vez instalado es
  un linter más del proyecto: se bloquea solo por violaciones en líneas
  que toca el diff.
- **Por rol, no todo para todos:** como cada rol ya es un `claude -p`
  separado, cada uno puede recibir sus propios servidores MCP.

Sin diseñar: si las herramientas se hornean en `docker/agent/Dockerfile`
(una imagen con todo) o se activan por rol al arrancar el contenedor, y el
costo en tiempo de build y tamaño, por proyecto, de todo lo que indexe el
checkout completo. El bloque de perfiles de tarea agrega una tercera
opción: las herramientas quedan en la imagen y cada `claude -p` carga solo
las de su rol y su perfil (`--mcp-config`, igual que `--plugin-dir`), sin
reiniciar el contenedor.

**Trabajo futuro — compactación a mitad de fase (fuera de alcance de este spec):**

Para la fase de un rol que llena su ventana de contexto antes de terminar
(p. ej. una implementación larga con muchas llamadas a herramientas):
vigilar el uso de contexto y, pasado un umbral, volcar a disco un resumen
específico del rol (para el Implementador: qué está hecho, estado actual,
qué falta, consideraciones), hacer `/clear` y reinyectar ese resumen.
Prioridad baja: Claude Code ya compacta solo una sesión que se acerca a su
límite, y `autoCompactWindow` (bloque de economía de tokens) mueve ese
umbral sin código, así que solo importa si se observa que las fases largas
fallan o se degradan igual. Hoy no hay dónde engancharlo: cada fase es un
único `claude -p ... --output-format json` no interactivo que devuelve un
JSON al salir, sin sesión viva en la que inyectar un `/clear`. Hacerlo de
verdad requiere una sesión conducida o en streaming (o un loop estilo SDK)
que el dispatcher observe turno a turno. Además solo ayuda *dentro* de la
fase de un rol: *entre* fases ya lo cubre el handoff de
`.hive/tasks/<task-id>.md` (mecanismo 1 de la sección 4a).

**Nota de prioridad sobre el workflow paralelo/balanceado:**

Con 2 cuentas Pro el límite es la cuota, no el throughput: correr ambas a la
vez sobre todo la gasta más rápido y suma conflictos de merge entre ramas
concurrentes. Conviene retomarlo con más cuentas, y preferir paralelizar
tareas independientes (vía `depends_on`) antes que partir una misma tarea.

**Trabajo futuro — contenedores agente multiproveedor (fuera de alcance de este spec):**

La menor prioridad: es lo que más trabajo lleva, y si el objetivo es más
capacidad, agregar otra cuenta Claude es solo configuración (el diseño ya
soporta N cuentas, ver *Decisiones de alcance*). Todo lo que está bajo
`docker/agent/`, `dispatcher/docker_exec.py` y `dispatcher/quota.py` es
específico de Claude: la imagen agente instala solo
`@anthropic-ai/claude-code`, las credenciales se aíslan por cuenta con el
volumen `claude_creds_<cuenta>` de esa cuenta, montado en
`CLAUDE_CONFIG_DIR` (sección 3), `exec_claude` invoca el binario `claude` con
`--output-format json`, y `quota.parse_usage_output` parsea el texto de
`/usage` tal cual. Extenderlo a otros CLIs (p. ej. Codex CLI, Gemini CLI)
requeriría, por proveedor:

- Una imagen agente propia (o un build arg `provider`) que instale ese CLI
  en vez de Claude Code o junto a él.
- Su propio volumen de credenciales por cuenta y su propio punto de
  montaje de config home, con el mismo patrón que `claude_creds_<cuenta>`
  pero apuntando al directorio de config de ese CLI (p. ej. `~/.codex`,
  `~/.gemini`) en vez de `CLAUDE_CONFIG_DIR`.
- Un campo `provider` en `AccountConfig` (`dispatcher/config.py`) y una
  abstracción de proveedor detrás de `docker_exec.exec_claude`, para
  invocar el binario y los flags correctos y parsear su session id,
  resultado y uso en vez de asumir el JSON de Claude Code.
- Confirmar que el CLI destino tiene un equivalente de
  `--resume <session_id>`: el mecanismo 2 de la sección 4a depende de eso
  para el relevo por agotamiento de cuota a mitad de rol.

Esto no está diseñado en detalle: los puntos de arriba son las costuras que
ya tiene la implementación actual, solo para Claude, no un spec.

## Cierre del proceso de diseño

- Las 8 secciones del diseño (control/UI, enrutamiento multi-proyecto,
  volúmenes/credenciales, smart dispatcher con sus dos subsecciones —
  incluyendo el hardening de DooD por sidecar `docker:dind` de la sección
  4b—, flujo operativo end-to-end, observabilidad, límites de
  recursos/convención de ramas, y alcance actual/trabajo futuro) están
  **[Aprobada]**. Los 4 pilares originales de `proyecto.md` quedan
  cubiertos.
- Auto-revisión del spec (placeholders, consistencia interna, alcance,
  ambigüedad) — completada.
- Revisión final del usuario — completada ("Apruebo la definición").
- `writing-plans` — invocado. El plan de implementación se guarda en
  `docs/superpowers/plans/2026-09-13-ia-harness.md`.
