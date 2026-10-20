"""`GET /api/notifications`, over the record `dispatcher/notifications.py` writes (ADR 221).

The mounts and the client are `test_api.py`'s `_harness`; the lines are
written by `notifications.append`, the one writer, so what is read is what
the board module and the dispatcher write.
"""

from __future__ import annotations

import json
from pathlib import Path

from dispatcher import notifications
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness

PATH = "/api/notifications"


def test_no_file_is_no_notification_and_no_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, PATH)

    assert resp.status_code == 200
    assert resp.get_json() == {"data": [], "warnings": []}


def test_notifications_are_served_newest_first_as_append_wrote_them(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    first = notifications.append(
        harness.tasks_dir, "awaiting_review", task="T-1", title="T-1 is waiting",
        url="/tasks/T-1",
    )
    second = notifications.append(
        harness.tasks_dir, "pool", account="cuenta1",
        title="account cuenta1 is available again", url="/pool",
    )

    body = _get(harness, PATH).get_json()

    assert body == {"data": [second, first], "warnings": []}


def test_a_line_that_is_not_a_notification_is_skipped_with_a_warning_naming_its_line(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    good = notifications.append(
        harness.tasks_dir, "merged", task="T-1", title="T-1 merged into main", url="/tasks/T-1"
    )
    path = Path(notifications.notifications_path(harness.tasks_dir))
    with path.open("ab") as fh:
        fh.write(b"{not json\n[1]\n")
        fh.write(json.dumps({**good, "kind": "made_up"}).encode() + b"\n")
        fh.write(json.dumps({**good, "account": "cuenta1"}).encode() + b"\n")
        fh.write(b"\n\xff\xfe\n")

    body = _get(harness, PATH).get_json()

    assert body["data"] == [good]
    assert [w.split(": ")[1] for w in body["warnings"]] == [
        "line 2", "line 3", "line 4", "line 5", "line 7",
    ]
    assert all(w.startswith(str(path)) for w in body["warnings"])


def test_the_answer_keeps_the_newest_and_names_the_clamp(tmp_path: Path, monkeypatch) -> None:
    harness = _harness(tmp_path)
    monkeypatch.setattr(api_app, "MAX_NOTIFICATIONS", 2)
    made = [
        notifications.append(
            harness.tasks_dir, "blocked", task=f"T-{n}", title=f"T-{n} is blocked",
            url=f"/tasks/T-{n}",
        )
        for n in range(3)
    ]

    body = _get(harness, PATH).get_json()

    assert body["data"] == [made[2], made[1]]
    assert any("at most 2" in w for w in body["warnings"])


def test_a_file_over_the_byte_cap_is_read_from_its_end(tmp_path: Path, monkeypatch) -> None:
    harness = _harness(tmp_path)
    made = [
        notifications.append(
            harness.tasks_dir, "merged", task=f"T-{n}", title=f"T-{n} merged", url=f"/tasks/T-{n}"
        )
        for n in range(3)
    ]
    size = Path(notifications.notifications_path(harness.tasks_dir)).stat().st_size
    # A cap that lands inside the first line: that line is cut whole.
    monkeypatch.setattr(api_app, "MAX_NOTIFICATIONS_BYTES", size - 10)

    body = _get(harness, PATH).get_json()

    assert body["data"] == [made[2], made[1]]
    assert len(body["warnings"]) == 1
    assert "not read" in body["warnings"][0]


def test_an_unknown_parameter_is_refused(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    assert _get(harness, PATH + "?kind=merged").status_code == 400


def test_the_route_needs_credentials(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    assert harness.client.get(PATH).status_code == 401
