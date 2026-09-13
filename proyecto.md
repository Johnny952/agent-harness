Descripción General del Sistema

Esta arquitectura define una plataforma multiagente autónoma, persisitente y 24/7, desplegada íntegramente en un servidor Linux local (Ubuntu Server con Tailscale y Coolify).

El sistema permite orquestar flujos de desarrollo en cascada (roles de Arquitecto, Implementador, Revisor y Auditor) utilizando múltiples instancias de Claude Code asociadas a diferentes suscripciones de Claude Pro. La solución resuelve de raíz la saturación del context window y la interrupción por límites de tokens (rate limits), manteniendo un aislamiento estricto de código y una interfaz de control accesible de forma remota y segura desde cualquier dispositivo.
Diagrama de Arquitectura

┌─────────────────────────────────────────────────────────────────────────┐
│ UBUNTU SERVER (24/7 Localhost) │
│ │
│ ┌───────────────────────────────────────────────────────────────────┐ │
│ │ CONDUCTOR.BUILD (UI Server-Side) │ │
│ │ • Dashboard Kanban, control visual de diffs y aprobaciones │ │
│ │ • Expuesto de forma privada a la Red Tailscale │ │
│ └─────────────────────────────────┬─────────────────────────────────┘ │
│ │ │
│ ▼ (docker exec local) │
│ ┌───────────────────────────────────────────────────────────────────┐ │
│ │ SMART DISPATCHER / ROUTER │ │
│ │ • Enruta prompts al contenedor activo según cuota / paralelismo │ │
│ └─────────────────┬───────────────────────────────┬─────────────────┘ │
│ │ │ │
│ ▼ ▼ │
│ ┌──────────────────────────────────┐ ┌──────────────────────────────┐ │
│ │ CONTENEDOR AGENTE 1 │ │ CONTENEDOR AGENTE 2 │ │
│ │ • Claude CLI (Cuenta Pro 1) │ │ • Claude CLI (Cuenta Pro 2) │ │
│ └─────────────────┬────────────────┘ └──────────────┬───────────────┘ │
│ │ │ │
│ └────────────────┬────────────────┘ │
│ │ │
│ ▼ │
│ ┌───────────────────────────────────────────────────────────────────┐ │
│ │ VOLUMEN COMPARTIDO & GIT WORKTREES │ │
│ │ • Código fuente del proyecto │ │
│ │ • Buzón de estados y checkpoints (`.hive/checkpoint.md`) │ │
│ │ • Socket de Docker host (DooD para entornos de pruebas) │ │
│ └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘

Pilares de la Arquitectura

1. Orquestación y Control Persistente (Conductor.build 24/7)

   Despliegue Server-Side: Conductor.build corre empaquetado dentro de su propio contenedor en el servidor Ubuntu.

   Acceso Remoto Ubicuo: La interfaz gráfica se disponibiliza a través de la IP privada de Tailscale. Puedes monitorear el trabajo de los agentes o autorizar merges desde una laptop, tablet o smartphone sin depender de mantener una sesión activa en la máquina cliente.

   Autonomía Total: Si apagas tu equipo personal, las tareas asignadas continúan ejecutándose en el servidor sin interrupciones.

2. Entornos de Agente Aislados (Docker Containers)

   Instancias Dedicadas: Cada agente/rol ejecuta Claude CLI dentro de un contenedor Docker ligero con montaje de volumen persistente (~/.claude).

   Convivencia con Coolify: Los contenedores no exponen puertos públicos a la red local; conviven en segundo plano con las aplicaciones administradas por Coolify sin generar colisiones.

   Docker-out-of-Docker (DooD): Mediante el montaje del socket /var/run/docker.sock, los agentes pueden levantar docker compose internos y redes de prueba para compilar la aplicación, ejecutar linters o correr suites de tests end-to-end dentro del servidor.

3. Rotación de Cuentas y Rate Limits (Sin violar T&C)

   Cumplimiento de Políticas: En lugar de usar proxies HTTP o wrappers no autorizados para simular APIs, cada contenedor mantiene iniciada una sesión oficial OAuth de una suscripción Claude Pro distinta (Cuenta Pro 1, Cuenta Pro 2).

   Smart Dispatcher: Un script intermediario analiza el estado de cada cuenta y el paralelismo requerido, despachando las ejecuciones docker exec al contenedor que disponga de cuota disponible. Esto duplica o triplica el techo operativo sin incurrir en costos de API por token.

4. Transferencia de Contexto Eficiente (Token-Saving Handoff)

   Comunicación por Disco: Para evitar arrastrar historiales de chat gigantescos que agotan la ventana de contexto y encarecen los turnos, los agentes se comunican a través de archivos en disco (.hive/checkpoint.md) y commits de Git.

   Cold Starts de Alto Rendimiento: Cuando finaliza una fase o se agota la cuota de la Cuenta Pro 1, la Cuenta Pro 2 despierta en su propio contenedor con su ventana de contexto al 0%, lee el resumen del checkpoint (~500 palabras) y retoma la implementación de forma inmediata con máxima precisión.

Flujo de Trabajo Operativo

    Definición de Tarea: Desde la Web UI de Conductor.build asignas un ticket o requerimiento.

    Fase de Arquitectura: El Contenedor 1 (Cuenta Pro 1) genera la especificación técnica en /docs y escribe la estrategia en .hive/checkpoint.md.

    Fase de Implementación: El Smart Dispatcher invoca al Contenedor 2 (Cuenta Pro 2) en un Git Worktree aislado para escribir el código y ejecutar pruebas usando el socket de Docker.

    Fase de Auditoría/Revisión: Una vez finalizado el código, se notifica al rol de Revisor/Auditor, quien valida el diff resultante frente a las especificaciones.

    Aprobación Final: Desde la interfaz de Conductor.build en tu navegador (vía Tailscale), inspeccionas los resultados y realizas el merge a la rama principal con un solo clic.
