#!/bin/sh
# docker/dind-sidecar/entrypoint.sh
# Start the prune cron daemon in the background, then hand PID 1 to the
# upstream dockerd entrypoint via exec so that:
#   - compose's `command:` (e.g. --registry-mirror=...) reaches dockerd as
#     "$@" instead of being swallowed by `sh -c`, and
#   - SIGTERM from `docker stop` reaches dockerd directly, so it shuts down
#     cleanly instead of being killed after the timeout.
set -e

crond -b || echo "warning: crond failed to start; nightly prune is disabled" >&2

exec dockerd-entrypoint.sh "$@"
