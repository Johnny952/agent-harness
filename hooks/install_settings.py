"""Register hooks/emit_event.py into Claude Code's settings.json.

Claude Code reads hook configuration from ``~/.claude/settings.json``. In the
agent image that directory is the ``claude_shared`` Docker volume mount point
(docker/compose/docker-compose.agents.yml), which shadows anything baked into
the image at ``/root/.claude`` — so the hook has to be registered when the
container starts (docker/agent/entrypoint.sh), not at build time.

The merge is idempotent and additive: hook groups already present in
settings.json are preserved, and the ia-harness command is only appended to an
event when it is not registered there yet.
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
    """Idempotently register `command` on every hook event in `path`."""
    settings_path = Path(path)
    settings = load_settings(settings_path)
    merged = merge_hooks(settings, build_hooks(command))
    if merged == settings:
        return merged
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = settings_path.with_name(settings_path.name + ".tmp")
    tmp_path.write_text(json.dumps(merged, indent=2) + "\n")
    os.replace(tmp_path, settings_path)
    return merged


def main() -> None:
    path = os.environ.get("CLAUDE_SETTINGS_PATH", DEFAULT_SETTINGS_PATH)
    install(path)
    print(f"ia-harness: event hook registered in {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
