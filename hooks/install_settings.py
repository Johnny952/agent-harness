"""Register hooks/emit_event.py into Claude Code's settings.json.

Claude Code reads hook configuration from settings.json in its config home
(``CLAUDE_CONFIG_DIR``, set per agent account in docker/agent/Dockerfile).
settings.json itself is real only in the ``claude_shared`` volume, at
``/root/.claude/settings.json``; docker/agent/entrypoint.sh symlinks it into
every account's config home, so all accounts share one hook registration.
The hook has to be registered when the container starts, not at build time,
because the volume mount shadows anything baked into the image at
``/root/.claude``.

Two things are written: the hook registration itself, and the
`syncClaudeAiSkills`/`syncClaudeAiPlugins` switches that keep the signed-in
account's claude.ai skills and plugins out of every role's prompt (see
SYNC_SETTINGS).

The merge is idempotent and additive: hook groups already present in
settings.json are preserved, the ia-harness command is only appended to an
event when it is not registered there yet, and a key the operator already set
is never rewritten. `install()` resolves its path with `os.path.realpath`
first, so writing through the shared-name symlink lands on the real file
instead of replacing the symlink with a private one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Absolute path the agent Dockerfile copies the hook script to. Plain
#: `python3` (no wrapper, no `|| true`): a broken hook should fail loudly
#: rather than silently stop emitting observability events.
HOOK_COMMAND = "python3 /usr/local/lib/ia-harness/emit_event.py"

DEFAULT_SETTINGS_PATH = "/root/.claude/settings.json"

#: Events whose hook groups are selected by a tool-name `matcher`.
TOOL_EVENTS = ("PreToolUse", "PostToolUse")

#: Events that carry no tool name, so their groups take no `matcher`.
SESSION_EVENTS = (
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
)


#: Settings that stop Claude Code from syncing the signed-in claude.ai
#: account's skills and plugins into this container. They are the only switch
#: that works: `CLAUDE_CODE_SYNC_SKILLS` is an enable gate rather than a kill
#: switch, and these keys are read from user or managed settings only, never
#: from a project's `.claude/settings.json` — which is exactly the layer the
#: shared settings.json occupies once the entrypoint symlinks it into each
#: account's config home. Only the literal `false` is honoured, so an
#: operator's `true` reads as "leave the sync alone" and works as the opt-in.
#:
#: Without them every role pays for skills nobody chose: 20 synced skills,
#: 13.5 kB of name and description in the system prompt of every turn of every
#: phase, re-synced every ten minutes — and, because one volume is shared,
#: each container also carries the other account's list. Turning the sync off
#: also moves the already-downloaded `skills/synced` to `skills/.trash` at the
#: next launch, so the copies leave the shared volume rather than sitting
#: there hidden.
SYNC_SETTINGS = {"syncClaudeAiSkills": False, "syncClaudeAiPlugins": False}


def build_hooks(command: str = HOOK_COMMAND) -> dict:
    """Return the `hooks` block registering `command` on every event."""
    entry = {"type": "command", "command": command}
    hooks: dict[str, list[dict]] = {}
    for event in TOOL_EVENTS:
        hooks[event] = [{"matcher": "*", "hooks": [entry]}]
    for event in SESSION_EVENTS:
        hooks[event] = [{"hooks": [entry]}]
    return hooks


def _has_command(groups: list, command: str) -> bool:
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks") or []:
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def merge_hooks(settings: dict, hooks: dict) -> dict:
    """Merge `hooks` into a copy of `settings` without dropping existing ones."""
    merged = dict(settings)
    existing_hooks = dict(merged.get("hooks") or {})
    for event, groups in hooks.items():
        current = list(existing_hooks.get(event) or [])
        for group in groups:
            command = group["hooks"][0]["command"]
            if not _has_command(current, command):
                current.append(group)
        existing_hooks[event] = current
    merged["hooks"] = existing_hooks
    return merged


def merge_defaults(settings: dict, defaults: dict) -> dict:
    """Return a copy of `settings` with every key `defaults` adds but it lacks.

    Additive like `merge_hooks`: a key the operator already set is left alone
    whatever its value, so settings.json stays the place to override this
    harness's opinion instead of a file the entrypoint wins back on every
    restart.
    """
    merged = dict(settings)
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def load_settings(path: Path) -> dict:
    """Read settings.json, treating a missing or empty file as no settings.

    A malformed settings.json raises: overwriting it would throw away whatever
    the operator configured (and Claude Code itself would reject it anyway).
    """
    if not path.exists():
        return {}
    text = path.read_text().strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def install(path: str = DEFAULT_SETTINGS_PATH, command: str = HOOK_COMMAND) -> dict:
    """Idempotently apply this harness's settings to `path`.

    Registers `command` on every hook event and adds whichever SYNC_SETTINGS
    keys the file does not already carry, in a single write that happens only
    if either merge changed something.

    `path` is resolved with `os.path.realpath` first, so when it is a
    symlink — for example the shared `settings.json` name symlinked into a
    per-account config home — the write lands on the symlink's target rather
    than replacing the symlink itself. This also holds for a dangling
    symlink, whose target does not exist yet.
    """
    settings_path = Path(os.path.realpath(path))
    settings = load_settings(settings_path)
    merged = merge_defaults(merge_hooks(settings, build_hooks(command)), SYNC_SETTINGS)
    if merged == settings:
        return merged
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = settings_path.with_name(settings_path.name + ".tmp")
    tmp_path.write_text(json.dumps(merged, indent=2) + "\n")
    os.replace(tmp_path, settings_path)
    return merged


def main() -> None:
    path = os.environ.get("CLAUDE_SETTINGS_PATH", DEFAULT_SETTINGS_PATH)
    settings = install(path)
    print(f"ia-harness: event hook registered in {path}", file=sys.stderr)
    for key, value in SYNC_SETTINGS.items():
        if settings.get(key) != value:
            # Not an error: the operator is allowed to want this. But it is
            # paid for on every turn of every phase, so it goes in the
            # container log rather than staying a surprise in the prompt.
            print(
                f"ia-harness: {key} is {json.dumps(settings.get(key))} in {path}, "
                f"not {json.dumps(value)}: this account's claude.ai items keep "
                "syncing into the shared volume and into every role's prompt.",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
