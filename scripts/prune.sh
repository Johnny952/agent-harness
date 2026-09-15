#!/bin/sh
# POSIX sh, not bash: this script runs inside the docker:dind (Alpine) image,
# where /bin/sh is BusyBox ash and `set -o pipefail` is unsupported.
set -eu

# Runs inside each docker:dind sidecar via cron (design spec sec. 4b) so the
# registry-mirror/BuildKit-cache disk savings aren't eaten by unused
# image/volume accumulation.

docker system prune -af --volumes
