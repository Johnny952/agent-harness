import io
import json
from pathlib import Path

from hooks import cap_subagent_return as cap


def _transcript(path: Path, user: str, assistant: str | None = None) -> str:
    rows = [{"type": "user", "message": {"content": user}}]
    if assistant is not None:
        rows.append({"type": "assistant", "message": {"content": [{"type": "text", "text": assistant}]}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def _payload(tmp_path: Path, text: str, **extra) -> dict:
    session = _transcript(tmp_path / "session.jsonl", "Role: implementador. Task: T-9.")
    return {
        "session_id": "s1",
        "agent_id": "a1",
        "transcript_path": session,
        "last_assistant_message": text,
        **extra,
    }


def _lines(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text().splitlines()]


def test_overlong_return_is_blocked_once_and_logged(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "r.jsonl"
    out = cap.decide(_payload(tmp_path, "x" * 4001), str(log))

    assert out["decision"] == "block"
    assert "4001" in out["reason"] and "4000" in out["reason"]
    (line,) = _lines(log)
    assert line["outcome"] == "blocked"
    assert (line["chars"], line["cap"]) == (4001, 4000)
    assert (line["role"], line["role_source"], line["task"]) == ("implementador", "session", "T-9")
    assert line["agent_id"] == "a1"


def test_retry_with_stop_hook_active_is_let_through_and_logged(tmp_path: Path) -> None:
    log = tmp_path / "r.jsonl"
    out = cap.decide(_payload(tmp_path, "x" * 5000, stop_hook_active=True), str(log))

    assert out is None
    (line,) = _lines(log)
    assert line["outcome"] == "retried" and line["chars"] == 5000


def test_retry_is_let_through_on_the_log_record_alone(tmp_path: Path) -> None:
    log = tmp_path / "r.jsonl"
    assert cap.decide(_payload(tmp_path, "x" * 5000), str(log)) is not None
    assert cap.decide(_payload(tmp_path, "x" * 4500), str(log)) is None
    assert [x["outcome"] for x in _lines(log)] == ["blocked", "retried"]


def test_return_under_the_cap_writes_nothing(tmp_path: Path) -> None:
    log = tmp_path / "r.jsonl"
    assert cap.decide(_payload(tmp_path, "x" * 4000), str(log)) is None
    assert not log.exists()


def test_role_sources_and_caps(tmp_path: Path, monkeypatch) -> None:
    sub = _transcript(tmp_path / "sub.jsonl", "do it <!-- ia-harness-role: auditor -->")
    payload = _payload(tmp_path, "hi", agent_transcript_path=sub)
    assert cap.role_of(payload) == ("auditor", "subagent", "T-9")

    assert cap.role_of(_payload(tmp_path, "hi")) == ("implementador", "session", "T-9")
    assert cap.role_of({}) == ("unknown", "none", None)

    monkeypatch.setitem(cap.CAPS, "auditor", 10)
    log = tmp_path / "r.jsonl"
    assert cap.decide(_payload(tmp_path, "x" * 11, agent_transcript_path=sub), str(log))
    assert _lines(log)[0]["cap"] == 10


def test_text_falls_back_to_the_subagent_transcript(tmp_path: Path) -> None:
    sub = _transcript(tmp_path / "sub.jsonl", "p", "y" * 4100)
    payload = _payload(tmp_path, "")
    del payload["last_assistant_message"]
    payload["agent_transcript_path"] = sub
    out = cap.decide(payload, str(tmp_path / "r.jsonl"))
    assert out is not None and "4100" in out["reason"]


def test_main_fails_open_and_prints_the_block(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IA_HARNESS_RETURN_LOG", str(tmp_path / "r.jsonl"))
    out = io.StringIO()
    cap.main([], stdin=io.StringIO("not json"), stdout=out)
    assert out.getvalue() == ""

    cap.main([], stdin=io.StringIO(json.dumps(_payload(tmp_path, "x" * 4001))), stdout=out)
    assert json.loads(out.getvalue())["decision"] == "block"


def test_report_summarises_per_role(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "r.jsonl"
    rows = [
        {"outcome": "blocked", "role": "auditor", "cap": 4000, "chars": 6000, "agent_id": "a"},
        {"outcome": "retried", "role": "auditor", "cap": 4000, "chars": 4500, "agent_id": "a"},
        {"outcome": "blocked", "role": "arquitecto", "cap": 4000, "chars": 4100, "agent_id": "b"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n")
    assert cap.report(str(log)) == [
        "role=arquitecto cap=4000 blocked=1 retried=0 retried_over=0 max_chars=4100",
        "role=auditor cap=4000 blocked=1 retried=1 retried_over=1 max_chars=6000",
    ]
    monkeypatch.setenv("IA_HARNESS_RETURN_LOG", str(log))
    out = io.StringIO()
    cap.main(["--report"], stdout=out)
    assert "role=auditor" in out.getvalue()
