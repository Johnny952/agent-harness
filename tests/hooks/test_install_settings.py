import json
from pathlib import Path

import pytest

from hooks import install_settings


def test_install_registers_hook_on_every_event(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    install_settings.install(str(settings_path))

    hooks = json.loads(settings_path.read_text())["hooks"]
    expected_events = set(install_settings.TOOL_EVENTS) | set(install_settings.SESSION_EVENTS)
    assert set(hooks) == expected_events
    for event in expected_events:
        commands = [h["command"] for group in hooks[event] for h in group["hooks"]]
        assert commands == [install_settings.HOOK_COMMAND]
    # Tool events select by tool name; the rest carry no matcher.
    for event in install_settings.TOOL_EVENTS:
        assert hooks[event][0]["matcher"] == "*"
    for event in install_settings.SESSION_EVENTS:
        assert "matcher" not in hooks[event][0]


def test_install_is_idempotent(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    install_settings.install(str(settings_path))
    first = settings_path.read_text()
    install_settings.install(str(settings_path))

    assert settings_path.read_text() == first
    hooks = json.loads(first)["hooks"]
    assert len(hooks["PreToolUse"]) == 1


def test_install_preserves_existing_settings_and_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}
                    ]
                },
            }
        )
    )

    install_settings.install(str(settings_path))

    settings = json.loads(settings_path.read_text())
    assert settings["model"] == "opus"
    commands = [h["command"] for group in settings["hooks"]["PreToolUse"] for h in group["hooks"]]
    assert commands == ["audit.sh", install_settings.HOOK_COMMAND]


def test_install_creates_parent_directory(tmp_path: Path) -> None:
    settings_path = tmp_path / "root" / ".claude" / "settings.json"

    install_settings.install(str(settings_path))

    assert settings_path.exists()


def test_install_refuses_to_clobber_malformed_settings(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not json")

    with pytest.raises(json.JSONDecodeError):
        install_settings.install(str(settings_path))

    assert settings_path.read_text() == "{not json"
