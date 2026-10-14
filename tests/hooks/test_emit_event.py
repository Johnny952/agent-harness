import io
import json

import pytest
import requests

import hooks.emit_event as emit_event_mod


class _Response:
    """The one attribute of `requests.Response` the hook reads."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _setup(monkeypatch, token: str | None = "write-token-a") -> dict:
    stdin_payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    monkeypatch.setattr(emit_event_mod.sys, "stdin", io.StringIO(json.dumps(stdin_payload)))
    monkeypatch.setenv("COLLECTOR_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("SOURCE_APP", "agent-cuenta1")
    if token is None:
        monkeypatch.delenv("COLLECTOR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("COLLECTOR_TOKEN", token)
    return stdin_payload


def test_main_posts_event_with_source_app_tag(monkeypatch) -> None:
    stdin_payload = _setup(monkeypatch)

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response(201)

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    emit_event_mod.main()

    assert captured["url"] == "http://127.0.0.1:8787/events"
    assert captured["json"] == {
        "source_app": "agent-cuenta1",
        "event_type": "PreToolUse",
        "payload": stdin_payload,
    }


def test_main_sends_its_write_token_as_a_bearer(monkeypatch) -> None:
    _setup(monkeypatch, token="write-token-a")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["headers"] = headers
        return _Response(201)

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    emit_event_mod.main()

    assert captured["headers"] == {"Authorization": "Bearer write-token-a"}


def test_main_with_no_token_sends_no_header_rather_than_raising(monkeypatch) -> None:
    # A `.env` written before ADR 72: the collector will 401, and the hook
    # must not fail the tool call on a KeyError first.
    _setup(monkeypatch, token=None)
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["headers"] = headers
        return _Response(401)

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    emit_event_mod.main()

    assert captured["headers"] == {}


def test_a_401_is_one_stderr_line_and_does_not_raise(monkeypatch, capsys) -> None:
    _setup(monkeypatch, token="wrong-token")
    monkeypatch.setattr(
        emit_event_mod.requests, "post", lambda url, json, headers, timeout: _Response(401)
    )

    emit_event_mod.main()

    err_lines = capsys.readouterr().err.strip("\n").split("\n")
    assert len(err_lines) == 1
    assert "refused" in err_lines[0]
    assert "401" in err_lines[0]


def test_a_2xx_prints_nothing(monkeypatch, capsys) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(
        emit_event_mod.requests, "post", lambda url, json, headers, timeout: _Response(201)
    )

    emit_event_mod.main()

    assert capsys.readouterr().err == ""


def test_main_survives_unreachable_collector(monkeypatch, capsys) -> None:
    _setup(monkeypatch)

    def fake_post(url, json, headers, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    emit_event_mod.main()

    err_lines = capsys.readouterr().err.strip("\n").split("\n")
    assert len(err_lines) == 1
    assert "collector" in err_lines[0]


def test_main_does_not_swallow_non_network_errors(monkeypatch) -> None:
    _setup(monkeypatch)

    def fake_post(url, json, headers, timeout):
        raise ValueError("not a network error")

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)

    with pytest.raises(ValueError):
        emit_event_mod.main()
