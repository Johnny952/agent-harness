#!/bin/sh
# docker/agent/entrypoint.sh
# The claude_shared volume is mounted at /root/.claude, which shadows anything
# the image baked in there — so the observability hook has to be registered
# into settings.json at start-up, once the volume is visible. The merge is
# idempotent, so restarts and a second agent sharing the same volume are fine.
set -e

python3 /usr/local/lib/ia-harness/install_settings.py

exec "$@"
