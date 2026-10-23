#!/usr/bin/env bash
# Put a backup back: scripts/restore.sh <name>|latest (docs/decisions.md ADR 328).
# Stops the worker, runs `dispatch restore` in the one-off dispatcher service with
# BACKUP_DIR mounted read-only at /restore (the service's own writable /backups
# stays apart from it, ADR 360), and starts the worker again only if it was running
# and the restore succeeded. A failed restore leaves the worker stopped.
# BACKUP_DIR and COMPOSE_FILE default as in scripts/backup.sh.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/restore.sh <name>|latest" >&2
  exit 2
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$(realpath -m "${BACKUP_DIR:-$REPO/.data/backups}")"
export COMPOSE_FILE="${COMPOSE_FILE:-$REPO/docker/compose/docker-compose.yml}"

running="$(docker compose --profile worker ps --status running -q worker)"
docker compose --profile worker stop worker

rc=0
docker compose run --rm -v "$BACKUP_DIR:/restore:ro" -e BACKUP_DIR=/restore \
  dispatcher --config /app/config.yaml restore --from "$1" || rc=$?

if [ "$rc" -ne 0 ]; then
  echo "restore failed (exit $rc): the worker is left stopped" >&2
  exit "$rc"
fi
if [ -n "$running" ]; then
  docker compose --profile worker up -d worker
else
  echo "the worker was not running; start it with: docker compose --profile worker up -d worker"
fi
