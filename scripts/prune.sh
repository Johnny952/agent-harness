#!/usr/bin/env bash
set -euo pipefail

# Runs inside each docker:dind sidecar via cron (design spec sec. 4b) so the
# registry-mirror/BuildKit-cache disk savings aren't eaten by unused
# image/volume accumulation.

docker system prune -af --volumes
