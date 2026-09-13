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

Documento origen: [`proyecto.md`](../../../proyecto.md) (propuesta inicial,
sin modificar — este spec reemplaza sus decisiones de diseño donde difieran,
p. ej. la UI de control).

## Decisiones de alcance

- **Escala objetivo:** 2 cuentas Claude Pro en uso inmediato, pero el diseño
  debe soportar N cuentas sin cambios estructurales.
- **Backlog:** vive únicamente en la herramienta de control (Vibe Kanban).
  No hay una fuente de verdad paralela (issues de GitHub, Markdown suelto,
  etc.) para el trabajo en curso.

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

**Failover:** al entrar una cuenta en `COOLING_DOWN`, su tarea en curso se
reencola hacia la próxima cuenta `IDLE`. Si todas las cuentas están
cerrando/cerradas, la tarea se marca visiblemente en Vibe Kanban como
"bloqueada, esperando cupo".

**Transferencia de contexto — dos mecanismos según el motivo del handoff:**

1. **Transición entre roles** (Arquitecto → Implementador, etc.): se
   mantiene `.hive/checkpoint.md` + commits de Git. Es un cold-start
   deliberado — el rol siguiente arranca con contexto al 0% y un resumen de
   ~500 palabras, lo cual es una ventaja (no arrastra el razonamiento interno
   del rol anterior), no una limitación.
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
   - `.hive/checkpoint.md` sigue como red de seguridad si la sesión no
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

- **Recomendación primaria:** runtime `sysbox` (`--runtime=sysbox-runc`).
  Evita exponer el socket del host y evita `--privileged`.
- **Fallback / riesgo residual aceptado explícitamente:** proxy
  `docker-socket-proxy` (Tecnativa) con allow-list estricta, para el caso en
  que `sysbox` no sea viable en el entorno final.

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
│  │ • .hive/checkpoint.md por tarea (handoff entre roles)               │ │
│  │ • socket Docker host (DooD, vía sysbox — sección 4b)                │ │
│  └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

**Secuencia operativa:**

1. **Definición de tarea:** desde Vibe Kanban se crea/asigna una tarjeta con
   el proyecto (y subproyecto si aplica) destino.
2. **Fase Arquitecto:** el dispatcher, según la máquina de estados, elige
   una cuenta disponible y ejecuta
   `docker exec -w .../worktrees/<task-id> <contenedor> claude -p "..."` con
   rol Arquitecto. Antes de despachar, consultó `/usage` de esa cuenta. La
   especificación resultante se escribe en el repo y el checkpoint en
   `.hive/checkpoint.md`.
3. **Handoff a Implementador:** cold-start deliberado vía checkpoint
   (mecanismo 1 de la sección 4a) — el dispatcher despacha al rol
   Implementador, mismo u otro contenedor/cuenta, sin arrastrar el contexto
   del Arquitecto.
4. **Durante la implementación, agotamiento de cuota (caso nuevo):** si la
   cuenta activa entra en `COOLING_DOWN` (proactivo por `/usage` o reactivo
   por error 429), el dispatcher reencola la tarea hacia la próxima cuenta
   `IDLE` usando `claude --resume <session_id>` (mecanismo 2 de la sección
   4a) — sin perder el trabajo en curso del rol Implementador. Si el resume
   falla, cae a checkpoint como red de seguridad.
5. **Fase Revisor/Auditor:** nuevo cold-start vía checkpoint, valida el diff
   contra la especificación del Arquitecto.
6. **Aprobación final:** desde Vibe Kanban (vía Tailscale), se inspecciona
   el resultado y se hace merge a la rama principal.

## Pendiente

- Las 5 secciones del diseño (control/UI, enrutamiento multi-proyecto,
  volúmenes/credenciales, smart dispatcher con sus dos subsecciones, y flujo
  operativo end-to-end) están **[Aprobada]**. Los 4 pilares originales de
  `proyecto.md` quedan cubiertos.
- Auto-revisión del spec (placeholders, consistencia interna, alcance,
  ambigüedad) — pendiente de ejecutar ahora que el diseño está cerrado.
- Revisión final por parte del usuario del spec completo.
- Recién después de la aprobación final: invocar `writing-plans` para el
  plan de implementación. Ninguna otra acción de implementación está
  autorizada antes de ese punto.
