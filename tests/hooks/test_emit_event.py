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


# --- redaction (ADR 74) -------------------------------------------------------

GH = "ghp_" + "a1B2c3D4e5" * 3 + "xyzXYZ"
ANT = "sk-ant-api03-AbCdEfGhIjKlMnOp"
AWS = "AKIAIOSFODNN7EXAMPLE"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


def _post(monkeypatch, event: dict) -> dict:
    """Run main() on `event`; return the body that was posted."""
    monkeypatch.setattr(emit_event_mod.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setenv("COLLECTOR_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("SOURCE_APP", "agent-cuenta1")
    monkeypatch.setenv("COLLECTOR_TOKEN", "write-token-a")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _Response(201)

    monkeypatch.setattr(emit_event_mod.requests, "post", fake_post)
    emit_event_mod.main()
    return captured["json"]


def _post_tool(monkeypatch, tool_input, tool_response=None, name="Bash") -> dict:
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": name,
        "tool_input": tool_input,
    }
    if tool_response is not None:
        event["tool_response"] = tool_response
    return _post(monkeypatch, event)


def test_command_line_token_and_bearer_are_redacted(monkeypatch) -> None:
    command = f'GITHUB_TOKEN={GH} curl -H "Authorization: Bearer abcdef1234567890" x'
    body = _post(
        monkeypatch,
        {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": command}},
    )
    text = json.dumps(body)
    assert GH not in text
    assert "abcdef1234567890" not in text
    assert "[REDACTED:github-token]" in text
    assert "Bearer [REDACTED:bearer-token]" in text
    assert body["payload"]["tool_name"] == "Bash"
    assert body["event_type"] == "PreToolUse"


def test_vendor_shapes_in_tool_response_are_redacted(monkeypatch) -> None:
    stdout = f"{ANT}\n{AWS}\n{PEM}\nAuthorization: Basic dXNlcjpwYXNz"
    body = _post_tool(
        monkeypatch,
        {"command": "env"},
        {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False},
    )
    text = json.dumps(body)
    for secret in (ANT, AWS, "MIIEowIBAAKCAQEA", "dXNlcjpwYXNz"):
        assert secret not in text
    for rule in ("anthropic-key", "aws-access-key-id", "private-key", "basic-auth"):
        assert f"[REDACTED:{rule}]" in text
    resp = body["payload"]["tool_response"]
    assert resp["interrupted"] is False and resp["isImage"] is False


def test_pem_without_end_line_is_cut_to_the_end(monkeypatch) -> None:
    body = _post_tool(
        monkeypatch,
        {"command": "cat k"},
        {"stdout": "x\n-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBg"},
    )
    text = json.dumps(body)
    assert "MIIBVQIBADANBg" not in text
    assert "[REDACTED:private-key]" in text


def test_read_of_dotenv_replaces_content_and_keeps_shape(monkeypatch) -> None:
    resp = {
        "type": "text",
        "file": {
            "filePath": "/w/.env",
            "content": "A=1\nB=2",
            "numLines": 2,
            "startLine": 1,
            "totalLines": 2,
        },
    }
    body = _post_tool(monkeypatch, {"file_path": "/w/.env"}, resp, name="Read")
    out = body["payload"]["tool_response"]
    assert out["file"]["content"] == "[REDACTED:secret-file]"
    assert out["file"]["filePath"] == "/w/.env"
    assert out["type"] == "text"
    assert out["file"]["numLines"] == 2
    assert "A=1" not in json.dumps(body)


def test_bash_cat_of_credentials_and_write_of_id_rsa(monkeypatch) -> None:
    body = _post_tool(
        monkeypatch,
        {"command": "cat config/credentials.json"},
        {"stdout": '{"user": "bob"}', "stderr": ""},
    )
    assert body["payload"]["tool_response"]["stdout"] == "[REDACTED:secret-file]"
    body = _post_tool(
        monkeypatch,
        {"file_path": "/home/u/.ssh/id_rsa", "content": "opaque-key-body"},
        name="Write",
    )
    assert body["payload"]["tool_input"]["content"] == "[REDACTED:secret-file]"
    assert body["payload"]["tool_input"]["file_path"] == "/home/u/.ssh/id_rsa"


def test_prompt_pairs_are_redacted(monkeypatch) -> None:
    body = _post(
        monkeypatch,
        {"hook_event_name": "UserPromptSubmit",
         "prompt": 'use {"password": "hunter22"} and DB_PASSWORD=x9 then run it'},
    )
    prompt = body["payload"]["prompt"]
    assert "hunter22" not in prompt and "x9" not in prompt
    assert '"password": "[REDACTED:secret-field]"' in prompt
    assert "DB_PASSWORD=[REDACTED:secret-assignment]" in prompt
    assert "then run it" in prompt


def test_secret_named_dict_key_redacts_string_but_not_numbers(monkeypatch) -> None:
    body = _post_tool(
        monkeypatch,
        {"api_key": "abc123", "input_tokens": 12, "monkey": "banana", "keyboard": "k"},
    )
    tool_input = body["payload"]["tool_input"]
    assert tool_input["api_key"] == "[REDACTED:secret-field]"
    assert tool_input["input_tokens"] == 12
    assert tool_input["monkey"] == "banana"
    assert tool_input["keyboard"] == "k"


def test_env_values_are_redacted_wherever_they_appear(monkeypatch) -> None:
    monkeypatch.setenv("MY_SECRET", "s3cr3t-value-123")
    monkeypatch.setenv("SOME_KEY", "1")
    body = _post_tool(
        monkeypatch,
        {"command": "echo hi 1"},
        {"stdout": "write-token-a and s3cr3t-value-123 and 1"},
    )
    out = body["payload"]["tool_response"]["stdout"]
    assert "write-token-a" not in out and "s3cr3t-value-123" not in out
    assert out == "[REDACTED:env-secret] and [REDACTED:env-secret] and 1"
    assert body["payload"]["tool_input"]["command"] == "echo hi 1"


def test_collector_token_never_appears_in_the_posted_body(monkeypatch) -> None:
    body = _post(
        monkeypatch,
        {"hook_event_name": "UserPromptSubmit", "prompt": "my token is write-token-a ok"},
    )
    assert "write-token-a" not in json.dumps(body)


def test_event_without_secrets_posts_byte_identical(monkeypatch) -> None:
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "session_id": "abc",
        "tool_input": {"file_path": "/w/src/app.py", "limit": 10},
        "tool_response": {"type": "text", "file": {"content": "x = 1\n", "numLines": 1}},
    }
    body = _post(monkeypatch, event)
    assert json.dumps(body["payload"]) == json.dumps(event)


def test_nested_dicts_and_lists_are_walked_and_shape_kept(monkeypatch) -> None:
    event = {
        "hook_event_name": "PostToolUse",
        "tool_input": {"edits": [{"old": f"t={GH}", "n": 3, "ok": True, "none": None}]},
        "tool_response": {"items": [["a", {"deep": f"Bearer {ANT}"}], 7, 1.5]},
    }
    body = _post(monkeypatch, event)
    payload = body["payload"]
    assert GH not in json.dumps(payload) and ANT not in json.dumps(payload)
    assert payload["tool_input"]["edits"][0]["n"] == 3
    assert payload["tool_input"]["edits"][0]["ok"] is True
    assert payload["tool_input"]["edits"][0]["none"] is None
    assert payload["tool_response"]["items"][1:] == [7, 1.5]
    assert payload["tool_response"]["items"][0][0] == "a"
    assert list(payload) == list(event)


def test_redaction_failure_posts_marker_and_no_secret(monkeypatch, capsys) -> None:
    def boom(event, env):
        raise RuntimeError(f"cannot handle {GH}")

    monkeypatch.setattr(emit_event_mod, "redact", boom)
    body = _post(
        monkeypatch,
        {"hook_event_name": "PreToolUse", "tool_input": {"command": f"x {GH}"}},
    )
    assert body["payload"] == {"redaction": "[REDACTED:redaction-failed]"}
    assert body["event_type"] == "PreToolUse"
    assert GH not in json.dumps(body)
    assert GH not in capsys.readouterr().err


def test_markers_carry_no_part_of_the_secret(monkeypatch) -> None:
    body = _post(
        monkeypatch,
        {"hook_event_name": "UserPromptSubmit", "prompt": f"{GH} {ANT} {AWS}"},
    )
    prompt = body["payload"]["prompt"]
    assert prompt == "[REDACTED:github-token] [REDACTED:anthropic-key] [REDACTED:aws-access-key-id]"


def test_text_starting_with_a_marker_is_still_redacted(monkeypatch) -> None:
    # A string that opens with marker-like text gets no pass: the rest of
    # it is redacted, and re-redacting a marker leaves it as it is.
    body = _post_tool(
        monkeypatch,
        {"command": f"echo '[REDACTED:x] {GH}'"},
        {"stdout": f"[REDACTED:x] {ANT}\nTOKEN=[REDACTED:secret-assignment]",
         "stderr": "", "interrupted": False, "isImage": False},
    )
    text = json.dumps(body)
    assert GH not in text
    assert ANT not in text
    assert body["payload"]["tool_response"]["stdout"] == (
        "[REDACTED:x] [REDACTED:anthropic-key]\n"
        "TOKEN=[REDACTED:secret-assignment]"
    )
