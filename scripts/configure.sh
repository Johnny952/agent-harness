#!/usr/bin/env bash
# scripts/configure.sh
#
# Interactive wizard that generates config.yaml, the agent/sidecar pairs in
# docker/compose/docker-compose.agents.yml (and optionally docker/compose/.env
# with the credentials the board, the read API and the collector check) for a
# fresh ia-harness checkout. Pure bash + coreutils (sha256sum) -- no
# Python required, so it can run directly on a fresh server before any image
# is built.
#
#   scripts/configure.sh [--advanced] [--config-out PATH] [--env-out PATH]
#                        [--agents-out PATH] [--coolify-out PATH]
#
# The agents file is rendered from the account list, one agent-<name> and
# dind-<name> pair per account, so a pool of one or of five comes up with
# `docker compose -f docker-compose.agents.yml up -d` and no hand-editing. The
# list the repo ships with (cuenta1,cuenta2) leaves the file alone: it is
# already that pool, and its comments are the ones a reader should get.
# docker/compose/docker-compose.coolify.yml, the one-resource Coolify deploy,
# is rendered from the same list on the same terms.
#
# Some config.yaml fields aren't really free-form: projects_root,
# hive_tasks_dir and state_dir must match the volume mounts in
# docker/compose/docker-compose*.yml, and collector_url is fixed by
# service-name DNS on the internal ia_harness_net network. This wizard
# defaults those to the values the compose files already assume and only
# asks about them under --advanced.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Must match the volume mounts / service DNS names baked into
# docker/compose/docker-compose.yml and docker-compose.agents.yml.
DEF_PROJECTS_ROOT="/data/projects"
DEF_HIVE_TASKS_DIR="/data/.hive/tasks"
DEF_STATE_DIR="/state"
DEF_COLLECTOR_URL="http://collector:8787"

# The account pairs docker-compose.agents.yml ships with out of the box.
COMPOSE_DEFAULT_ACCOUNTS="cuenta1,cuenta2"


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
    # Matches observability/auth.py's check_auth, which compares
    # DASHBOARD_PASSWORD_HASH against hashlib.sha256(password).hexdigest()
    # directly -- a plain digest, NOT a salted/method-prefixed hash. Using
    # anything else here would silently lock the operator out of the board and
    # the read API both, which share this one credential.
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$1" | sha256sum | cut -d' ' -f1
    else
        printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
    fi
}

gen_token() {
    # API_TOKEN: what the board presents to the read API so it never holds the
    # operator's plaintext password (the API checks a hash, so basic auth from a
    # service would need the plaintext in .env beside the hash and make the hash
    # decorative -- docs/decisions.md ADR 7). Compared with
    # secrets.compare_digest, so any long random string will do; 32 bytes of hex
    # is what openssl gives without asking.
    #
    # `command -v` with a fallback, the same shape hash_password uses above, for
    # the same reason: a fresh server has one of these two and it is not always
    # the same one.
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

write_token_var() {
    # The `.env` key holding one account's collector write token
    # (docs/decisions.md ADR 72): the account name uppercased, `-` as `_`, so
    # cuenta1 is COLLECTOR_WRITE_TOKEN_CUENTA1 -- the name the compose files
    # hand to agent-cuenta1 as COLLECTOR_TOKEN and bind to it in the collector's
    # COLLECTOR_WRITE_TOKENS.
    printf 'COLLECTOR_WRITE_TOKEN_%s' "$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')"
}

render_agents_compose() {
    # render_agents_compose NAME... -> prints docker-compose.agents.yml for
    # that pool. Kept line for line with the shipped file -- every mount,
    # limit, runtime and environment entry, in the same order -- so a rendered
    # pool differs from the shipped one only in its names; tests/scripts/
    # test_configure.py holds the two-account render equal to the shipped file
    # and runs the compose invariants on a one-account render. The comments of
    # the shipped file are given once, on the first agent; every other pair
    # points back to it, as agent-cuenta2 does to agent-cuenta1.
    first=$1
    cat <<EOF
# docker/compose/docker-compose.agents.yml
# Generated by scripts/configure.sh for the accounts: $*.
# One agent+sidecar block per Claude Pro account. Run scripts/configure.sh
# again to change the pool rather than editing this by hand -- no dispatcher
# code changes required (design spec, "Decisiones de alcance": N accounts
# without structural changes).
#
# Bring up docker-compose.yml first: it creates \`ia_harness_net\`, which this
# file joins as an external network so agents and sidecars can resolve
# \`collector\` and \`registry-mirror\` by service name.
services:
EOF
    for name in "$@"; do
        token_var=$(write_token_var "$name")
        if [ "$name" = "$first" ]; then
            cat <<EOF
  agent-$name:
    image: ia-harness-agent
    container_name: agent-$name
    restart: unless-stopped
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_HOST=tcp://dind-$name:2375
      # Consumed by hooks/emit_event.py, registered into Claude Code's
      # settings.json by the agent image entrypoint.
      - COLLECTOR_URL=http://collector:8787
      - SOURCE_APP=agent-$name
      # This agent's write token (docs/decisions.md ADR 72), sent by the hook
      # as a bearer. The collector binds it to agent-$name in
      # COLLECTOR_WRITE_TOKENS (docker-compose.yml), so it cannot post as any
      # other agent, and it cannot read: no agent is given COLLECTOR_READ_TOKEN.
      - COLLECTOR_TOKEN=\${$token_var}
    volumes:
      # Names every account shares (session history, skills/agents/commands/
      # plugins, settings.json) -- symlinked into CLAUDE_CONFIG_DIR by the
      # agent image entrypoint. Never holds a login; see claude_creds_$name.
      - claude_shared:/root/.claude
      # This account's Claude Code config home (CLAUDE_CONFIG_DIR, set in
      # docker/agent/Dockerfile). Holds .credentials.json, .claude.json and
      # everything else per-account (except .device-keys.json, whose path the
      # CLI hardcodes to /root/.claude), so $name's login stays out of
      # claude_shared and out of every other account's reach.
      - claude_creds_$name:/root/.claude-account
      # Repo-relative bind mount (not a named volume) so cloned project repos
      # live at <repo-root>/.data/projects on the host, inspectable directly.
      # NOTE: the agent image has no USER directive, so files written here
      # from inside the container will show up root-owned on the host.
      - ../../.data/projects:/data/projects
      # Agents read their task file (lock owner, role, handoff notes) from
      # here; the dispatcher is the only writer. Path matches config.yaml's
      # \`hive_tasks_dir: /data/.hive/tasks\`.
      - ../../.hive:/data/.hive
      # Read-only credential for the project's own remote, used by the
      # dispatcher's \`update_project_before_task\` to fast-forward a project's
      # checkout before a task starts (dispatcher/docker_exec.py's
      # update_project_branch). On the host and not in the image, because these
      # containers are built to be thrown away and a file on the host outlives
      # them and is shared by every account without logging each one in; a mount
      # and not an environment variable, because run_docker_exec passes env as
      # \`-e KEY=value\` on the \`docker exec\` argv, where any \`ps\` reads it. A
      # public remote over https:// needs no credential and a missing file is
      # not a stop, so this mount is allowed to point at nothing; but a bind
      # mount with no source gets a directory created for it, so a file meant to
      # be read has to exist BEFORE these come up, and an empty directory left
      # at that path has to go first. It is gitignored (.gitignore ignores
      # .data/), one line in git's \`store\` format. The token needs read access,
      # and write (contents) access for a project the dispatcher pushes, since
      # it pushes the base with the same credential as the fetch (ADR 78).
      - ../../.data/credentials/git-credentials:/run/secrets/git-credentials:ro
      # Each project's SSH deploy key, at <slug>/id_ed25519, made by the
      # dispatcher's \`project-keygen\` (docs/plans/projects.md, Decisions 2).
      # Read-only here: an agent uses a key, it never makes or replaces one. A
      # directory mount, so the missing-source trap of the file above does not
      # apply: Docker creates it empty, and a project with no key in it falls
      # back to the https:// credential.
      - ../../.data/credentials/projects:/run/secrets/projects:ro
    depends_on:
      - dind-$name
    networks:
      - ia_harness_net

EOF
        else
            cat <<EOF
  agent-$name:
    image: ia-harness-agent
    container_name: agent-$name
    restart: unless-stopped
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_HOST=tcp://dind-$name:2375
      - COLLECTOR_URL=http://collector:8787
      - SOURCE_APP=agent-$name
      - COLLECTOR_TOKEN=\${$token_var}
    volumes:
      # See agent-$first above: claude_shared is the read/write-shared
      # allowlist, claude_creds_$name is this account's own config home.
      - claude_shared:/root/.claude
      - claude_creds_$name:/root/.claude-account
      - ../../.data/projects:/data/projects
      - ../../.hive:/data/.hive
      # See agent-$first: the same host file, shared, read-only.
      - ../../.data/credentials/git-credentials:/run/secrets/git-credentials:ro
      - ../../.data/credentials/projects:/run/secrets/projects:ro
    depends_on:
      - dind-$name
    networks:
      - ia_harness_net

EOF
        fi
        cat <<EOF
  dind-$name:
    image: ia-harness-dind-sidecar
    container_name: dind-$name
    restart: unless-stopped
    runtime: sysbox-runc
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_TLS_CERTDIR=
    command: ["--registry-mirror=http://registry-mirror:5000"]
    volumes:
      - dind_${name}_data:/var/lib/docker
      # The projects tree at the agent's path. The agent runs \`docker compose\`
      # against this daemon from /data/projects/<slug>, and the daemon resolves
      # the project's bind mounts on its own filesystem, so the path must exist
      # here too. A \`service\` command request's project lives here (ADR 118).
      - ../../.data/projects:/data/projects
    networks:
      - ia_harness_net

EOF
    done
    cat <<EOF
networks:
  # Created by docker-compose.yml. \`external: true\` means this file joins it
  # rather than creating a second, isolated network.
  ia_harness_net:
    external: true
    name: ia_harness_net

volumes:
  claude_shared:
    external: true
EOF
    for name in "$@"; do
        echo "  claude_creds_$name:"
        echo "    external: true"
    done
    for name in "$@"; do
        echo "  dind_${name}_data:"
    done
}

render_coolify_compose() {
    # render_coolify_compose SOURCE NAME... -> prints docker-compose.coolify.yml
    # for that pool. Everything above the first agent pair (header, collector,
    # api, console, registry-mirror, dispatcher) is SOURCE's own, kept as it is but for
    # the collector's COLLECTOR_WRITE_TOKENS line, which is rebuilt to bind
    # exactly these agents; the agent pairs and the volumes are rendered here.
    # Unlike the agents file, the map stays a list of ${...} references: a
    # Coolify resource takes its variables from Coolify's own UI, one per
    # account, not from the .env this script writes. tests/scripts/
    # test_configure.py holds the two-account render equal to the shipped
    # file and runs the compose invariants on a one-account render.
    source_file=$1
    shift
    first=$1
    token_map=""
    for name in "$@"; do
        token_map="${token_map:+$token_map,}agent-$name=\${$(write_token_var "$name")}"
    done
    awk -v map="$token_map" '
        /^  agent-/ { exit }
        /^      - COLLECTOR_WRITE_TOKENS=/ { print "      - COLLECTOR_WRITE_TOKENS=" map; next }
        { print }
    ' "$source_file"
    for name in "$@"; do
        token_var=$(write_token_var "$name")
        if [ "$name" = "$first" ]; then
            cat <<EOF
  agent-$name:
    build:
      context: ../..
      dockerfile: docker/agent/Dockerfile
    container_name: agent-$name
    restart: unless-stopped
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_HOST=tcp://dind-$name:2375
      - COLLECTOR_URL=http://collector:8787
      - SOURCE_APP=agent-$name
      # This agent's write token and no other (ADR 72); never the read token.
      - COLLECTOR_TOKEN=\${$token_var}
    volumes:
      # See the header comment above: claude_shared is the shared allowlist,
      # claude_creds_$name is this account's own config home.
      - claude_shared:/root/.claude
      - claude_creds_$name:/root/.claude-account
      - ../../.data/projects:/data/projects
      - ../../.hive:/data/.hive
      # Each project's SSH deploy key, read-only; see docker-compose.agents.yml's
      # agent-cuenta1 comment. The https:// git-credentials file is not mounted
      # here (ADR 91).
      - ../../.data/credentials/projects:/run/secrets/projects:ro
    depends_on:
      - dind-$name
    networks:
      - ia_harness_net

EOF
        else
            cat <<EOF
  agent-$name:
    build:
      context: ../..
      dockerfile: docker/agent/Dockerfile
    container_name: agent-$name
    restart: unless-stopped
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_HOST=tcp://dind-$name:2375
      - COLLECTOR_URL=http://collector:8787
      - SOURCE_APP=agent-$name
      - COLLECTOR_TOKEN=\${$token_var}
    volumes:
      # See agent-$first above.
      - claude_shared:/root/.claude
      - claude_creds_$name:/root/.claude-account
      - ../../.data/projects:/data/projects
      - ../../.hive:/data/.hive
      # See agent-$first above: the deploy keys, read-only.
      - ../../.data/credentials/projects:/run/secrets/projects:ro
    depends_on:
      - dind-$name
    networks:
      - ia_harness_net

EOF
        fi
        cat <<EOF
  dind-$name:
    build:
      context: ../dind-sidecar
      dockerfile: Dockerfile
    container_name: dind-$name
    restart: unless-stopped
    runtime: sysbox-runc
    cpus: "2"
    mem_limit: "4g"
    environment:
      - DOCKER_TLS_CERTDIR=
    command: ["--registry-mirror=http://registry-mirror:5000"]
    volumes:
      - dind_${name}_data:/var/lib/docker
      # The projects tree at the agent's path. The agent runs \`docker compose\`
      # against this daemon from /data/projects/<slug>, and the daemon resolves
      # the project's bind mounts on its own filesystem, so the path must exist
      # here too. A \`service\` command request's project lives here (ADR 118).
      - ../../.data/projects:/data/projects
    networks:
      - ia_harness_net

EOF
    done
    cat <<EOF
networks:
  ia_harness_net:
    name: ia_harness_net
  console_net:

volumes:
  observability_data:
  registry_mirror_data:
  claude_shared:
EOF
    for name in "$@"; do
        echo "  claude_creds_$name:"
    done
    for name in "$@"; do
        echo "  dind_${name}_data:"
    done
}

# Sourced (tests/scripts/test_configure.py does, to render a pool without
# running the wizard), the file stops here with its functions defined.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

ADVANCED=0
CONFIG_OUT="$REPO_ROOT/config.yaml"
ENV_OUT="$REPO_ROOT/docker/compose/.env"
AGENTS_OUT="$REPO_ROOT/docker/compose/docker-compose.agents.yml"
COOLIFY_SOURCE="$REPO_ROOT/docker/compose/docker-compose.coolify.yml"
COOLIFY_OUT="$COOLIFY_SOURCE"

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
        --agents-out)
            AGENTS_OUT="$2"
            shift 2
            ;;
        --coolify-out)
            COOLIFY_OUT="$2"
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
    # Two names that differ only in '-' against '_' are two containers but
    # one .env key, so the second account's write token would overwrite the
    # first's and one agent would post under the other's name, or be refused.
    seen_vars=()
    seen_names=()
    for name in "${ACCOUNT_NAMES[@]:-}"; do
        [ -z "$name" ] && continue
        var=$(write_token_var "$name")
        for i in "${!seen_vars[@]}"; do
            if [ "${seen_vars[$i]}" = "$var" ]; then
                echo "  - '${seen_names[$i]}' and '$name' would share the .env key $var ('-' and '_' are the same there); rename one" >&2
                invalid=1
            fi
        done
        seen_vars+=("$var")
        seen_names+=("$name")
    done
    if [ "$invalid" -eq 1 ]; then
        continue
    fi
    if [ "${#ACCOUNT_NAMES[@]}" -gt 0 ]; then
        break
    fi
    echo "Enter at least one account name."
done

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
    } > "$CONFIG_OUT"
    echo "Wrote $CONFIG_OUT"
fi

# config.d, beside config.yaml, whether or not it was just written
# (docs/decisions.md ADR 110 part 9). The config-writer mounts it read-write
# and the dispatcher and api mount it :ro; if it does not exist at the first
# `up`, docker creates it root-owned and the operator needs sudo to repair a
# file in it by hand. Made here it is the operator's. Never chmod'd: the root
# writer reaches it either way.
mkdir -p "$(dirname "$CONFIG_OUT")/config.d"

# The list the repo ships with is the shipped agents file already; rendering
# it again would only trade its comments for the generated ones.
sorted_input=$(printf '%s\n' "${ACCOUNT_NAMES[@]}" | sort | paste -sd, -)
sorted_default=$(printf '%s\n' cuenta1 cuenta2 | sort | paste -sd, -)
write_pool_file() {
    # write_pool_file PATH RENDERED -> RENDERED into PATH, through
    # confirm_overwrite when PATH exists. Rendered before the write, because
    # the Coolify file is rendered from the file it may be about to replace.
    if [ -e "$1" ] && ! confirm_overwrite "$1"; then
        echo "Left $1 untouched. Its agent pairs no longer match config.yaml's accounts."
        return 0
    fi
    mkdir -p "$(dirname "$1")"
    printf '%s\n' "$2" > "$1"
    echo "Wrote $1"
}

if [ "$sorted_input" = "$sorted_default" ]; then
    echo "$AGENTS_OUT already holds the cuenta1/cuenta2 pairs; left it untouched."
    echo "$COOLIFY_OUT already holds the cuenta1/cuenta2 pairs; left it untouched."
else
    write_pool_file "$AGENTS_OUT" "$(render_agents_compose "${ACCOUNT_NAMES[@]}")"
    write_pool_file "$COOLIFY_OUT" "$(render_coolify_compose "$COOLIFY_SOURCE" "${ACCOUNT_NAMES[@]}")"
fi

echo
echo "== Web credentials =="
echo "One username and password for the read API (127.0.0.1:8789)."
echo "The variables keep their DASHBOARD_ names: renaming them would mean editing the .env of every"
echo "running host to buy a spelling. The console gets its own token for calling the API."
setup_credentials=$(prompt_yes_no "Set up HTTP Basic Auth now (writes docker/compose/.env)?" "y")
if [ "$setup_credentials" = "y" ]; then
    DASHBOARD_USERNAME=$(prompt_text "Username" "admin")
    while true; do
        read -r -s -p "Password: " password
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
    API_TOKEN=$(gen_token)

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
            echo "# Consumed by the \`api\` service in docker-compose.yml."
            echo "# The DASHBOARD_ names outlived the dashboard on purpose: a rename"
            echo "# means editing this file on every running host to buy a spelling."
            echo "DASHBOARD_USERNAME=$DASHBOARD_USERNAME"
            echo "DASHBOARD_PASSWORD_HASH=$PASSWORD_HASH"
            echo "# How the console authenticates to the api, so it never holds"
            echo "# the password above. The console reads the same value from its"
            echo "# own environment; rotate it by changing both and restarting."
            echo "API_TOKEN=$API_TOKEN"
            echo "# How the console authenticates to the config-writer service"
            echo "# (127.0.0.1:8791; docs/decisions.md ADR 75, ADR 109). Required: the"
            echo "# writer refuses to start without it. Given to it and the console only."
            echo "CONFIG_WRITER_TOKEN=$(gen_token)"
            # The collector's tokens (docs/decisions.md ADR 72). Required, like
            # API_TOKEN: the collector refuses to start without them. Generated
            # here with the rest because a hand-picked token is a guessable one.
            echo "# The operator's token for GET /events on the collector"
            echo "# (127.0.0.1:8787). Given to the collector and no other service."
            echo "COLLECTOR_READ_TOKEN=$(gen_token)"
            echo "# One write token per account: given to that agent as"
            echo "# COLLECTOR_TOKEN and to the collector, which stores the account"
            echo "# it is bound to. An agent's token cannot read, nor post as another."
            # Each token is generated once and written twice: under the
            # account's own key, which the agent reads, and in the collector's
            # map. The map spells the values out rather than as ${...}: a
            # literal map does not depend on how compose expands one .env
            # entry inside another, and it is what lets docker-compose.yml
            # take any pool without an edit.
            token_map=""
            for name in "${ACCOUNT_NAMES[@]}"; do
                token=$(gen_token)
                echo "$(write_token_var "$name")=$token"
                token_map="${token_map:+$token_map,}agent-$name=$token"
            done
            echo "# The collector's whole map, one agent-<name>=<token> entry per"
            echo "# account above, with the same values."
            echo "COLLECTOR_WRITE_TOKENS=$token_map"
            echo "# TLS (docs/decisions.md ADR 100), plain compose only. Uncomment to serve"
            echo "# a domain over HTTPS; unset, Caddy serves http://localhost."
            echo "# HARNESS_DOMAIN=console.example.com"
            echo "# ACME_EMAIL=you@example.com"
            echo "# The console's Basic auth through Caddy (docs/decisions.md ADR 103)."
            echo "# Required: with no hash Caddy refuses to start. Make one with"
            echo "#   docker run --rm -it caddy:2.10.2 caddy hash-password"
            echo "# and paste it in single quotes: compose expands a \$ in anything else."
            echo "# CONSOLE_AUTH_USER=console"
            echo "# CONSOLE_AUTH_HASH='\$2a\$14\$...'"
        } > "$ENV_OUT"
        echo "Wrote $ENV_OUT"
    fi
fi

echo
echo "Next steps:"
echo "  # The same account list: it creates claude_creds_<name> for each, which"
echo "  # the agents file mounts as an external volume."
echo "  scripts/setup_volumes.sh ${ACCOUNT_NAMES[*]}"
echo "  docker build -f docker/agent/Dockerfile -t ia-harness-agent ."
echo "  docker build -f docker/dind-sidecar/Dockerfile -t ia-harness-dind-sidecar docker/dind-sidecar"
echo "  docker compose -f docker/compose/docker-compose.yml up -d"
echo "  # Brings up the agents file this script generated (or left as shipped):"
echo "  # one agent-<name>/dind-<name> pair per account above."
echo "  docker compose -f $AGENTS_OUT up -d"
echo "  # Or, deploying with Coolify instead: import $COOLIFY_OUT as one"
echo "  # resource and set the .env values above in its environment-variables UI."
