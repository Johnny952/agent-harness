#!/usr/bin/env bash
# Copy the hive and the dispatcher's state into $BACKUP_DIR (docs/decisions.md
# ADR 327). Runs `dispatch backup` in the one-off dispatcher service, which
# mounts BACKUP_DIR itself (ADR 360), so the run line carries no -v or -e.
#   BACKUP_DIR   host directory for the copies (default <repo>/.data/backups)
#   BACKUP_KEEP  copies to keep, oldest deleted first (default 7)
#   COMPOSE_FILE the compose file (default docker/compose/docker-compose.yml)
# Exit 75: a cycle is running; the next run retries.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Absolute, because compose reads it for the mount and resolves a relative one
# against the compose file, not against the caller's directory.
BACKUP_DIR="$(realpath -m "${BACKUP_DIR:-$REPO/.data/backups}")"
export BACKUP_DIR
export COMPOSE_FILE="${COMPOSE_FILE:-$REPO/docker/compose/docker-compose.yml}"
mkdir -p "$BACKUP_DIR"

exec docker compose run --rm dispatcher --config /app/config.yaml backup
