import io
import json

import hooks.emit_event as emit_event_mod


def test_main_posts_event_with_source_app_tag(monkeypatch) -> None:
    stdin_payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    monkeypatch.setattr(emit_event_mod.sys, "stdin", io.StringIO(json.dumps(stdin_payload)))
    monkeypatch.setenv("COLLECTOR_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("SOURCE_APP", "agent-cuenta1")

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    emit_event_mod.main()

    assert captured["url"] == "http://127.0.0.1:8787/events"
    assert captured["json"] == {
        "source_app": "agent-cuenta1",
        "event_type": "PreToolUse",
        "payload": stdin_payload,
    }
