#!/usr/bin/env bash
set -euo pipefail

# Creates the shared ~/.claude volume (claude_shared) plus one per-account
# config-home volume (claude_creds_<account>, mounted at /root/.claude-account
# via CLAUDE_CONFIG_DIR) per account (design spec sec. 3). Run once before the
# first `docker compose -f docker-compose.agents.yml up`.
#
# projects_data is NOT created here: docker-compose.agents.yml bind-mounts
# <repo-root>/.data/projects instead of a named volume, so cloned project
# repos live inside this checkout. We still create that directory ourselves
# (rather than letting Docker auto-create it on first mount) so it's owned
# by the invoking user instead of root.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ACCOUNTS=("$@")
if [ ${#ACCOUNTS[@]} -eq 0 ]; then
    echo "Usage: $0 <account1> [account2 ...]" >&2
    exit 1
fi

docker volume inspect claude_shared >/dev/null 2>&1 || docker volume create claude_shared
mkdir -p "$REPO_ROOT/.data/projects"

for account in "${ACCOUNTS[@]}"; do
    vol="claude_creds_${account}"
    docker volume inspect "$vol" >/dev/null 2>&1 || docker volume create "$vol"
    echo "Volume ready: $vol (mounted at /root/.claude-account (CLAUDE_CONFIG_DIR) for ${account})"
done
