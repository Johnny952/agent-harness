#!/usr/bin/env bash
# Copy the hive and the dispatcher's state into $BACKUP_DIR (docs/decisions.md
# ADR 327). Runs `dispatch backup` in the one-off dispatcher service, with the
# host directory mounted on the run line so no compose file changes.
#   BACKUP_DIR   host directory for the copies (default <repo>/.data/backups)
#   BACKUP_KEEP  copies to keep, oldest deleted first (default 7)
#   COMPOSE_FILE the compose file (default docker/compose/docker-compose.yml)
# Exit 75: a cycle is running; the next run retries.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO/.data/backups}"
export COMPOSE_FILE="${COMPOSE_FILE:-$REPO/docker/compose/docker-compose.yml}"
mkdir -p "$BACKUP_DIR"

run=(docker compose run --rm -v "$BACKUP_DIR:/backups" -e BACKUP_DIR=/backups)
if [ -n "${BACKUP_KEEP:-}" ]; then
  run+=(-e "BACKUP_KEEP=$BACKUP_KEEP")
fi
exec "${run[@]}" dispatcher --config /app/config.yaml backup
