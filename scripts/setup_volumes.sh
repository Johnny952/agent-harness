#!/usr/bin/env bash
set -euo pipefail

# Creates the shared ~/.claude volume plus one shadowed credentials volume
# per account (design spec sec. 3). Run once before the first
# `docker compose -f docker-compose.agents.yml up`.

ACCOUNTS=("$@")
if [ ${#ACCOUNTS[@]} -eq 0 ]; then
    echo "Usage: $0 <account1> [account2 ...]" >&2
    exit 1
fi

docker volume inspect claude_shared >/dev/null 2>&1 || docker volume create claude_shared
docker volume inspect projects_data >/dev/null 2>&1 || docker volume create projects_data

for account in "${ACCOUNTS[@]}"; do
    vol="claude_creds_${account}"
    docker volume inspect "$vol" >/dev/null 2>&1 || docker volume create "$vol"
    echo "Volume ready: $vol (shadow-mount at /root/.claude/credentials for ${account})"
done
