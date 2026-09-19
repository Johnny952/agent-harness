#!/bin/sh
# docker/agent/entrypoint.sh
#
# Each agent's Claude Code config home is $CLAUDE_CONFIG_DIR (set in the
# image, see docker/agent/Dockerfile), backed by that account's
# claude_creds_<account> volume. That is where the OAuth .credentials.json,
# .claude.json and other per-account state live. /root/.claude (the
# claude_shared volume) holds only the names every account shares -- session
# history, skills/agents/commands/plugins, and settings.json -- and this
# script symlinks those in. Anything not on that allowlist stays private to
# the account by default.
#
# The checks below fail loudly and refuse to start rather than silently
# falling back to the shared volume: a silent fallback is exactly the V0.4
# defect this replaced (the login ended up in claude_shared, readable by
# every agent). `restart: unless-stopped` turns a failure here into a
# visible restart loop instead of a quiet cross-account login leak.
set -e

# --- Guards ---------------------------------------------------------------

if [ -z "${CLAUDE_CONFIG_DIR:-}" ]; then
    echo "ia-harness: CLAUDE_CONFIG_DIR is unset or empty; it must name this account's config home (see docker/agent/Dockerfile)." >&2
    exit 1
fi

case "$CLAUDE_CONFIG_DIR" in
    /*) ;;
    *)
        echo "ia-harness: CLAUDE_CONFIG_DIR ('$CLAUDE_CONFIG_DIR') must be an absolute path." >&2
        exit 1
        ;;
esac

# Normalize a trailing slash before comparing against /root/.claude.
config_dir="${CLAUDE_CONFIG_DIR%/}"

case "$config_dir" in
    /root/.claude | /root/.claude/*)
        echo "ia-harness: CLAUDE_CONFIG_DIR ('$CLAUDE_CONFIG_DIR') must not be /root/.claude or a path under it; that volume is shared by every agent and must not hold a login." >&2
        exit 1
        ;;
esac

if ! mountpoint -q "$config_dir"; then
    echo "ia-harness: CLAUDE_CONFIG_DIR ('$CLAUDE_CONFIG_DIR') is not a mount point. Without its own volume the login would live in the container layer and be lost on recreate." >&2
    exit 1
fi

if [ -n "${CLAUDE_SECURESTORAGE_CONFIG_DIR:-}" ]; then
    echo "ia-harness: CLAUDE_SECURESTORAGE_CONFIG_DIR is set ('$CLAUDE_SECURESTORAGE_CONFIG_DIR'); unset it, it would move .credentials.json out of CLAUDE_CONFIG_DIR." >&2
    exit 1
fi

# --- Shared directories -----------------------------------------------------
# Created up front so the symlinks below never dangle for a directory.

shared_dirs="projects todos file-history session-env plans skills agents commands plugins output-styles"

for name in $shared_dirs; do
    mkdir -p "/root/.claude/$name"
done

# --- Observability hook -----------------------------------------------------
# Registers the hook in /root/.claude/settings.json (the shared file, later
# symlinked into $CLAUDE_CONFIG_DIR below). Idempotent, so restarts and a
# second agent sharing the volume are both fine.

python3 /usr/local/lib/ia-harness/install_settings.py

# --- Symlink the shared allowlist into this account's config home ----------

shared_names="projects todos file-history session-env plans skills agents commands plugins output-styles settings.json CLAUDE.md"

for name in $shared_names; do
    link="$config_dir/$name"
    target="/root/.claude/$name"

    if [ -L "$link" ]; then
        current=$(readlink "$link")
        if [ "$current" = "$target" ]; then
            continue
        fi
        echo "ia-harness: $link is a symlink to '$current', not '$target'. Move its contents into $target (merging if needed), remove $link, then restart." >&2
        exit 1
    fi

    if [ -e "$link" ]; then
        echo "ia-harness: $link already exists and is not the expected symlink to $target. Move its contents into $target (merging if needed), remove $link, then restart." >&2
        exit 1
    fi

    # A dangling link (e.g. CLAUDE.md before anyone has written one) is
    # fine: the CLI treats a missing CLAUDE.md as absent.
    ln -s "$target" "$link"
done

# --- Warn about leftovers in the shared volume ------------------------------
# Never moved or read here: two agents starting at once would race over one
# file, and only the operator knows which account a leftover login belongs
# to. See the README's "Upgrading from the shared-login layout" note.
#
# .device-keys.json is deliberately not in this list: the CLI hardcodes its
# path to ~/.claude regardless of CLAUDE_CONFIG_DIR, so it is never a leftover
# from the old layout -- it is a permanent, known exception to per-account
# isolation, and warning about it would never clear on a healthy install.

for leftover in /root/.claude/.credentials.json /root/.claude/.claude.json /root/.claude/backups; do
    if [ -e "$leftover" ]; then
        echo "ia-harness: $leftover is still in the shared claude_shared volume and readable by every agent. See the README's 'Upgrading from the shared-login layout' note." >&2
    fi
done

exec "$@"
