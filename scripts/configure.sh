#!/usr/bin/env bash
# scripts/configure.sh
#
# Interactive wizard that generates config.yaml (and optionally
# docker/compose/.env with hashed dashboard credentials) for a fresh
# ia-harness checkout. Pure bash + coreutils (sha256sum) -- no Python
# required, so it can run directly on a fresh server before any image is
# built.
#
#   scripts/configure.sh [--advanced] [--config-out PATH] [--env-out PATH]
#
# Some config.yaml fields aren't really free-form: projects_root,
# hive_tasks_dir and state_dir must match the volume mounts in
# docker/compose/docker-compose*.yml, and collector_url is fixed by
# service-name DNS on the internal ia_harness_net network. This wizard
# defaults those to the values the compose files already assume and only
# asks about them under --advanced.
#
# It writes neither board block. A board is optional, the dispatcher runs
# without one, and there are two to choose between: `local_board`, a
# directory of JSON cards that needs no service, and `vibe_kanban`, which
# needs a command that exists. They are alternatives — configuring both is a
# startup error — so the choice is the operator's, and the generated config
# points at config.example.yaml for either.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Must match the volume mounts / service DNS names baked into
# docker/compose/docker-compose.yml and docker-compose.agents.yml.
DEF_PROJECTS_ROOT="/data/projects"
DEF_HIVE_TASKS_DIR="/data/.hive/tasks"
DEF_STATE_DIR="/state"
DEF_COLLECTOR_URL="http://collector:8787"

# The account pairs docker-compose.agents.yml ships with out of the box.
COMPOSE_AGENTS_FILE="docker/compose/docker-compose.agents.yml"
COMPOSE_DEFAULT_ACCOUNTS="cuenta1,cuenta2"

ADVANCED=0
CONFIG_OUT="$REPO_ROOT/config.yaml"
ENV_OUT="$REPO_ROOT/docker/compose/.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --advanced)
            ADVANCED=1
            shift
            ;;
        --config-out)
            CONFIG_OUT="$2"
            shift 2
            ;;
        --env-out)
            ENV_OUT="$2"
            shift 2
            ;;
        -h|--help)
            grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

prompt_text() {
    # prompt_text QUESTION DEFAULT -> prints the answer
    question=$1
    default=$2
    while true; do
        read -r -p "$question [$default]: " answer || true
        answer=$(echo -n "$answer" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [ -n "$answer" ]; then
            printf '%s\n' "$answer"
            return
        fi
        if [ -n "$default" ]; then
            printf '%s\n' "$default"
            return
        fi
        echo "This field is required." >&2
    done
}

prompt_int() {
    question=$1
    default=$2
    while true; do
        read -r -p "$question [$default]: " answer || true
        answer=$(echo -n "$answer" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [ -z "$answer" ]; then
            printf '%s\n' "$default"
            return
        fi
        if echo "$answer" | grep -Eq '^[0-9]+$'; then
            printf '%s\n' "$answer"
            return
        fi
        echo "Please enter a whole number." >&2
    done
}

prompt_yes_no() {
    # prompt_yes_no QUESTION DEFAULT(y|n) -> prints y or n
    question=$1
    default=$2
    hint="y/N"
    [ "$default" = "y" ] && hint="Y/n"
    while true; do
        read -r -p "$question [$hint]: " answer || true
        answer=$(echo "$answer" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [ -z "$answer" ]; then
            printf '%s\n' "$default"
            return
        fi
        case "$answer" in
            y|yes) printf 'y\n'; return ;;
            n|no) printf 'n\n'; return ;;
            *) echo "Please answer y or n." >&2 ;;
        esac
    done
}

# parse_account_names RAW -> prints deduped, trimmed names, one per line
parse_account_names() {
    raw=$1
    old_ifs=$IFS
    IFS=','
    set -- $raw
    IFS=$old_ifs
    seen=""
    for part in "$@"; do
        name=$(echo "$part" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [ -z "$name" ] && continue
        case ",$seen," in
            *",$name,"*) continue ;;
        esac
        seen="$seen,$name"
        printf '%s\n' "$name"
    done
}

# validate_account_name NAME -> prints an error and returns 1 if invalid
validate_account_name() {
    name=$1
    if ! echo "$name" | grep -Eq '^[a-z0-9]([a-z0-9_-]*[a-z0-9])?$'; then
        echo "  - '$name' is not a valid account name (lowercase letters, digits, '-' and '_' only, must start/end alphanumeric)" >&2
        return 1
    fi
    return 0
}

confirm_overwrite() {
    path=$1
    if [ ! -e "$path" ]; then
        return 0
    fi
    result=$(prompt_yes_no "$path already exists. Overwrite?" "n")
    [ "$result" = "y" ]
}

hash_password() {
    # Matches observability/dashboard/app.py's check_auth, which compares
    # DASHBOARD_PASSWORD_HASH against hashlib.sha256(password).hexdigest()
    # directly -- a plain digest, NOT a salted/method-prefixed hash. Using
    # anything else here would silently lock the operator out of their own
    # dashboard.
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$1" | sha256sum | cut -d' ' -f1
    else
        printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
    fi
}

echo "ia-harness interactive configuration"
echo

echo "== Accounts =="
echo "Each account is a Claude Pro login, run in its own agent-<name> container (see docker-compose.agents.yml)."
while true; do
    raw=$(prompt_text "Account names (comma-separated)" "$COMPOSE_DEFAULT_ACCOUNTS")
    ACCOUNT_NAMES=()
    while IFS= read -r line; do
        ACCOUNT_NAMES+=("$line")
    done < <(parse_account_names "$raw")

    invalid=0
    for name in "${ACCOUNT_NAMES[@]:-}"; do
        [ -z "$name" ] && continue
        validate_account_name "$name" || invalid=1
    done
    if [ "$invalid" -eq 1 ]; then
        continue
    fi
    if [ "${#ACCOUNT_NAMES[@]}" -gt 0 ]; then
        break
    fi
    echo "Enter at least one account name."
done

sorted_input=$(printf '%s\n' "${ACCOUNT_NAMES[@]}" | sort | paste -sd, -)
sorted_default=$(printf '%s\n' cuenta1 cuenta2 | sort | paste -sd, -)
if [ "$sorted_input" != "$sorted_default" ]; then
    echo
    echo "Note: $COMPOSE_AGENTS_FILE ships with pairs for cuenta1, cuenta2 only. This script does not edit that file -- duplicate/remove the agent-<name>/dind-<name> service pairs there to match, and pass the matching account names to scripts/setup_volumes.sh."
    echo
fi

echo
echo "== Dispatcher tuning =="
QUOTA_THRESHOLD_PCT=$(prompt_int "Quota threshold percent" 90)
HEARTBEAT_TTL_SECONDS=$(prompt_int "Heartbeat TTL seconds" 120)
HEARTBEAT_INTERVAL_SECONDS=$(prompt_int "Heartbeat interval seconds" 30)

PROJECTS_ROOT=$DEF_PROJECTS_ROOT
HIVE_TASKS_DIR=$DEF_HIVE_TASKS_DIR
STATE_DIR=$DEF_STATE_DIR
COLLECTOR_URL=$DEF_COLLECTOR_URL

if [ "$ADVANCED" -eq 1 ]; then
    echo
    echo "== Advanced: paths/URLs fixed by docker-compose volume mounts and service DNS =="
    echo "Only change these if you edited the compose files' mounts/network to match."
    PROJECTS_ROOT=$(prompt_text "projects_root" "$DEF_PROJECTS_ROOT")
    HIVE_TASKS_DIR=$(prompt_text "hive_tasks_dir" "$DEF_HIVE_TASKS_DIR")
    STATE_DIR=$(prompt_text "state_dir" "$DEF_STATE_DIR")
    COLLECTOR_URL=$(prompt_text "collector_url" "$DEF_COLLECTOR_URL")
fi

write_config=1
if [ -e "$CONFIG_OUT" ]; then
    if ! confirm_overwrite "$CONFIG_OUT"; then
        write_config=0
        echo "Left $CONFIG_OUT untouched."
    fi
fi

if [ "$write_config" -eq 1 ]; then
    {
        echo "accounts:"
        for name in "${ACCOUNT_NAMES[@]}"; do
            echo "  - name: $name"
            echo "    container: agent-$name"
        done
        echo "quota_threshold_pct: $QUOTA_THRESHOLD_PCT"
        echo "heartbeat_ttl_seconds: $HEARTBEAT_TTL_SECONDS"
        echo "heartbeat_interval_seconds: $HEARTBEAT_INTERVAL_SECONDS"
        echo "projects_root: $PROJECTS_ROOT"
        echo "hive_tasks_dir: $HIVE_TASKS_DIR"
        echo "state_dir: $STATE_DIR"
        echo "collector_url: $COLLECTOR_URL"
        echo "# A board is optional and left unconfigured here. To mirror tasks"
        echo "# onto one, copy a commented block from config.example.yaml:"
        echo "# \`local_board\` is a directory of JSON cards and needs only a"
        echo "# \`dir\` on a mounted volume; \`vibe_kanban\` talks to a Vibe Kanban"
        echo "# server and needs its command runnable. They are alternatives:"
        echo "# configuring both blocks is a startup error."
    } > "$CONFIG_OUT"
    echo "Wrote $CONFIG_OUT"
fi

echo
echo "== Dashboard credentials =="
setup_dashboard=$(prompt_yes_no "Set up dashboard HTTP Basic Auth now (writes docker/compose/.env)?" "y")
if [ "$setup_dashboard" = "y" ]; then
    DASHBOARD_USERNAME=$(prompt_text "Dashboard username" "admin")
    while true; do
        read -r -s -p "Dashboard password: " password
        echo
        if [ -z "$password" ]; then
            echo "Password can't be empty."
            continue
        fi
        read -r -s -p "Confirm password: " confirm
        echo
        if [ "$password" != "$confirm" ]; then
            echo "Passwords didn't match, try again."
            continue
        fi
        break
    done
    PASSWORD_HASH=$(hash_password "$password")

    write_env=1
    if [ -e "$ENV_OUT" ]; then
        if ! confirm_overwrite "$ENV_OUT"; then
            write_env=0
            echo "Left $ENV_OUT untouched."
        fi
    fi
    if [ "$write_env" -eq 1 ]; then
        mkdir -p "$(dirname "$ENV_OUT")"
        {
            echo "# Generated by scripts/configure.sh"
            echo "# Consumed by the \`dashboard\` service in docker-compose.yml."
            echo "DASHBOARD_USERNAME=$DASHBOARD_USERNAME"
            echo "DASHBOARD_PASSWORD_HASH=$PASSWORD_HASH"
        } > "$ENV_OUT"
        echo "Wrote $ENV_OUT"
    fi
fi

echo
echo "Next steps:"
echo "  scripts/setup_volumes.sh ${ACCOUNT_NAMES[*]}"
echo "  docker build -f docker/agent/Dockerfile -t ia-harness-agent ."
echo "  docker build -f docker/dind-sidecar/Dockerfile -t ia-harness-dind-sidecar docker/dind-sidecar"
echo "  docker compose -f docker/compose/docker-compose.yml up -d"
echo "  docker compose -f docker/compose/docker-compose.agents.yml up -d"
