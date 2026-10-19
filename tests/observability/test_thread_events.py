"""`GET /api/threads/<id>/events`, against a temporary hive (ADR 173, 180).

The mounts and the client are `test_api.py`'s `_harness`. Lines are written by
`dispatcher/threads.py`'s own `append_conversation`, the worker's writer, so a
change to the log's format shows up here; the torn and broken ones by hand.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dispatcher import threads as thread_store
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness

PATH = "/api/threads/main/events"


def _log(harness, *lines, thread: str = "main") -> None:
    for line in lines:
        thread_store.append_conversation(harness.tasks_dir, thread, line)


def _turn(text: str) -> list[dict]:
    return [
        {"type": "ia_harness", "event": "turn_start", "text": text},
        {"type": "system", "subtype": "init", "session_id": "s-1"},
        {"type": "result", "subtype": "success", "result": "ok"},
        {"type": "ia_harness", "event": "turn_end", "status": "done"},
    ]


def test_no_log_yet_is_an_empty_list_and_makes_nothing(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    body = _get(harness, PATH).get_json()

    assert body == {"data": [], "warnings": []}
    conversations = os.path.dirname(thread_store.conversation_path(harness.tasks_dir, "main"))
    assert not os.path.exists(conversations)


def test_a_row_is_the_line_number_and_the_line_as_written(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    turn = _turn("hola")
    _log(harness, *turn)

    rows = _get(harness, PATH).get_json()["data"]

    assert rows == [{"n": n, "event": e} for n, e in enumerate(turn, start=1)]


def test_since_serves_the_lines_after_it_and_limit_bounds_them(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _log(harness, *_turn("one"), *_turn("two"))

    rows = _get(harness, f"{PATH}?since=3&limit=2").get_json()["data"]
    assert [r["n"] for r in rows] == [4, 5]
    assert rows[1]["event"]["text"] == "two"

    assert _get(harness, f"{PATH}?since=8").get_json() == {"data": [], "warnings": []}
    assert [r["n"] for r in _get(harness, f"{PATH}?since=-5").get_json()["data"]] == list(
        range(1, 9)
    )


def test_a_final_line_still_being_written_is_left_for_the_next_read(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _log(harness, *_turn("hola")[:2])
    path = thread_store.conversation_path(harness.tasks_dir, "main")
    with open(path, "a") as f:
        f.write('{"type": "assist')

    body = _get(harness, PATH).get_json()

    assert [r["n"] for r in body["data"]] == [1, 2]
    assert body["warnings"] == []

    with open(path, "a") as f:
        f.write('ant"}\n')
    rows = _get(harness, f"{PATH}?since=2").get_json()["data"]
    assert rows == [{"n": 3, "event": {"type": "assistant"}}]


def test_a_whole_line_that_will_not_parse_is_a_null_row_and_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _log(harness, {"type": "ia_harness", "event": "turn_start"}, "{not json", "[1, 2]",
         {"type": "result"})

    body = _get(harness, PATH).get_json()

    assert [(r["n"], r["event"]) for r in body["data"][1:3]] == [(2, None), (3, None)]
    assert body["data"][3] == {"n": 4, "event": {"type": "result"}}
    assert [w.rsplit(": ", 1)[1] for w in body["warnings"]] == [
        "line 2 is not a JSON object",
        "line 3 is not a JSON object",
    ]


def test_threads_have_their_own_logs(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _log(harness, {"type": "ia_harness", "event": "turn_start", "text": "main"})
    _log(harness, {"type": "ia_harness", "event": "turn_start", "text": "other"},
         thread="th-20261019T120000Z-abc123")

    [row] = _get(harness, "/api/threads/th-20261019T120000Z-abc123/events").get_json()["data"]

    assert row["event"]["text"] == "other"


def test_an_id_the_harness_does_not_make_is_400_and_reaches_no_path(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    for thread_id in ("..", "th-abc123", "main.jsonl"):
        resp = _get(harness, f"/api/threads/{thread_id}/events")

        assert resp.status_code == 400, thread_id
        assert "no thread id" in resp.get_json()["error"]
    assert not os.path.exists(os.path.join(harness.tasks_dir, thread_store.CONVERSATIONS_DIR_NAME))


def test_a_limit_above_the_maximum_is_clamped_and_says_so(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_app, "MAX_THREAD_EVENT_LIMIT", 2)
    harness = _harness(tmp_path)
    _log(harness, *_turn("hola"))

    body = _get(harness, f"{PATH}?limit=3").get_json()

    assert [r["n"] for r in body["data"]] == [1, 2]
    [warning] = body["warnings"]
    assert "limit=3" in warning and "since=<last n>" in warning


def test_bad_parameters_are_400(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    assert _get(harness, f"{PATH}?limit=0").status_code == 400
    assert _get(harness, f"{PATH}?since=soon").status_code == 400


def test_a_log_path_that_cannot_be_read_is_a_warning_and_not_a_500(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    path = Path(thread_store.conversation_path(harness.tasks_dir, "main"))
    path.mkdir(parents=True)

    resp = _get(harness, PATH)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == []
    [warning] = body["warnings"]
    assert "unreadable conversation log" in warning
    assert json.dumps(body)  # the envelope still serializes
