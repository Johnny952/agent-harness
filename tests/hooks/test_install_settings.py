import json
import os
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


def test_install_through_symlink_writes_target_and_keeps_link(tmp_path: Path) -> None:
    target = tmp_path / "shared" / "settings.json"
    target.parent.mkdir()
    link = tmp_path / "account" / "settings.json"
    link.parent.mkdir()
    link.symlink_to(target)

    install_settings.install(str(link))

    assert link.is_symlink()
    assert os.path.realpath(link) == str(target)
    hooks = json.loads(target.read_text())["hooks"]
    commands = [h["command"] for group in hooks["PreToolUse"] for h in group["hooks"]]
    assert commands == [install_settings.HOOK_COMMAND]


def test_install_through_dangling_symlink_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "shared" / "settings.json"
    link = tmp_path / "account" / "settings.json"
    link.parent.mkdir()
    link.symlink_to(target)  # target, and even its parent dir, don't exist yet

    install_settings.install(str(link))

    assert link.is_symlink()
    assert os.path.realpath(link) == str(target)
    assert target.exists()
    hooks = json.loads(target.read_text())["hooks"]
    commands = [h["command"] for group in hooks["PreToolUse"] for h in group["hooks"]]
    assert commands == [install_settings.HOOK_COMMAND]


def test_install_turns_off_claude_ai_sync(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    install_settings.install(str(settings_path))

    settings = json.loads(settings_path.read_text())
    # The literal false is the only value Claude Code honours here.
    assert settings["syncClaudeAiSkills"] is False
    assert settings["syncClaudeAiPlugins"] is False


def test_install_keeps_an_operator_set_sync_value(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"syncClaudeAiSkills": True}))

    install_settings.install(str(settings_path))

    settings = json.loads(settings_path.read_text())
    # settings.json is where the opt-in lives: an operator who wants their
    # claude.ai skills in the containers says so here, and the entrypoint
    # re-running on every restart does not argue with them.
    assert settings["syncClaudeAiSkills"] is True
    assert settings["syncClaudeAiPlugins"] is False
    assert settings["hooks"]["PreToolUse"]


def test_install_leaves_settings_alone_when_sync_is_already_off(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    install_settings.install(str(settings_path))
    first = settings_path.read_text()
    install_settings.install(str(settings_path))

    assert settings_path.read_text() == first


def test_main_warns_when_the_sync_is_left_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"syncClaudeAiSkills": True}))
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings_path))

    install_settings.main()

    err = capsys.readouterr().err
    assert "syncClaudeAiSkills" in err
    assert "syncClaudeAiPlugins" not in err


def test_main_is_quiet_when_the_sync_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings_path))

    install_settings.main()

    err = capsys.readouterr().err
    assert "syncClaudeAi" not in err
