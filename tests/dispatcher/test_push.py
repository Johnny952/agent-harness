"""The push notifier (ADR 229): the cursor, the kinds, the tag, the grouping, the drop.

The send is a stub: nothing here imports pywebpush or reaches the network.
"""
import base64
import datetime as dt
import json
import logging
import zoneinfo
from pathlib import Path

import pytest

from dispatcher import notifications, push


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sub(name: str) -> dict:
    return {
        "endpoint": f"https://push.example.com/{name}",
        "keys": {"p256dh": b64(b"\x04" + b"\x01" * 64), "auth": b64(b"\x02" * 16)},
    }


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Sender:
    def __init__(self, status=201) -> None:
        self.status = status
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, subscription, payload, *, key_path, subject, ttl, headers):
        self.calls.append((subscription["endpoint"], json.loads(payload), headers))
        status = self.status(subscription) if callable(self.status) else self.status
        if isinstance(status, Exception):
            raise status
        return status


@pytest.fixture
def hive(tmp_path: Path) -> str:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return str(path)


@pytest.fixture
def key(tmp_path: Path) -> str:
    path = tmp_path / "vapid.pem"
    path.write_text("a key; the stub never reads it\n")
    return str(path)


def notifier(hive, key, send, clock=None, **kw) -> push.Notifier:
    return push.Notifier(hive, key_path=key, subject="mailto:x@y", send=send,
                         clock=clock or Clock(), **kw)


def started(hive, key, send, clock=None, **kw) -> push.Notifier:
    """A notifier past its first tick, which only sets the cursor at the end."""
    n = notifier(hive, key, send, clock, **kw)
    push.add(hive, sub("a"))
    assert n.tick() == 0
    return n


def note(hive, kind="awaiting_review", task="t-1", **kw):
    if "thread" in kw or "account" in kw:
        return notifications.append(hive, kind, title=f"{kind} title", url="/u", **kw)
    return notifications.append(hive, kind, title=f"{task} {kind}", url=f"/tasks/{task}",
                                task=task)


def test_the_first_run_starts_at_the_end_and_sends_no_history(hive, key):
    note(hive)
    send = Sender()
    n = notifier(hive, key, send)
    push.add(hive, sub("a"))
    assert n.tick() == 0
    assert send.calls == []
    size = Path(notifications.notifications_path(hive)).stat().st_size
    assert json.loads(Path(push.cursor_path(hive)).read_text()) == {"offset": size}


def test_a_new_record_goes_to_every_subscription_with_its_tag(hive, key):
    send = Sender()
    n = started(hive, key, send)
    push.add(hive, sub("b"))
    record = note(hive, task="t-9")
    assert n.tick() == 2
    assert [c[0] for c in send.calls] == [sub("a")["endpoint"], sub("b")["endpoint"]]
    payload, headers = send.calls[0][1], send.calls[0][2]
    assert payload == {"id": record["id"], "kind": "awaiting_review",
                       "title": record["title"], "url": "/tasks/t-9", "tag": "task:t-9"}
    assert headers["Urgency"] == "high"
    assert len(headers["Topic"]) == 32
    assert n.tick() == 0  # The cursor moved past it.


def test_the_tag_follows_the_subject(hive, key):
    send = Sender()
    n = started(hive, key, send)
    note(hive, "merged", task="t-1")
    note(hive, "pool", account="cuenta2")
    note(hive, "command_request", task="t-2")
    n.tick()
    tags = {c[1]["kind"]: (c[1]["tag"], c[2]["Urgency"]) for c in send.calls}
    assert tags == {"merged": ("task:t-1", "normal"), "pool": ("account:cuenta2", "normal"),
                    "command_request": ("task:t-2", "high")}


def test_kinds_off_by_default_are_not_pushed(hive, key):
    send = Sender()
    n = started(hive, key, send)
    note(hive, "phase_failed")
    note(hive, "turn_finished", thread="th-1")
    assert n.tick() == 0
    assert send.calls == []


def test_records_of_one_kind_within_the_window_are_grouped(hive, key):
    clock = Clock()
    send = Sender()
    n = started(hive, key, send, clock)
    note(hive, task="t-1")
    n.tick()
    assert send.calls[-1][1]["tag"] == "task:t-1"

    clock.now += 60
    note(hive, task="t-2")
    note(hive, task="t-3")
    note(hive, "merged", task="t-4")
    n.tick()
    grouped = [c[1] for c in send.calls[1:]]
    assert grouped[0]["title"] == "3 tasks awaiting review"
    assert grouped[0]["tag"] == "kind:awaiting_review"
    assert grouped[0]["url"] == "/notifications"
    # Another kind, alone in its window, is its own push.
    assert grouped[1]["tag"] == "task:t-4"
    assert len(grouped) == 2

    clock.now += push.GROUP_WINDOW_SECONDS + 1
    note(hive, task="t-5")
    n.tick()
    assert send.calls[-1][1]["tag"] == "task:t-5"


def test_several_new_records_of_one_kind_at_once_are_one_push(hive, key):
    send = Sender()
    n = started(hive, key, send)
    note(hive, "conflict", task="t-1")
    note(hive, "conflict", task="t-2")
    assert n.tick() == 1
    assert send.calls[0][1]["title"] == "2 merge conflicts"


@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_subscription_is_dropped(hive, key, status):
    send = Sender(lambda s: status if s["endpoint"].endswith("/a") else 201)
    n = started(hive, key, send)
    push.add(hive, sub("b"))
    note(hive)
    assert n.tick() == 1
    assert [s["endpoint"] for s in push.read_subscriptions(hive)] == [sub("b")["endpoint"]]


def test_a_failed_send_is_kept_and_never_raises(hive, key, caplog):
    send = Sender(lambda s: RuntimeError("boom") if s["endpoint"].endswith("/a") else 500)
    n = started(hive, key, send)
    push.add(hive, sub("b"))
    note(hive)
    with caplog.at_level(logging.WARNING, logger="dispatcher.push"):
        assert n.tick() == 0
    assert len(push.read_subscriptions(hive)) == 2
    assert "boom" in caplog.text and "answered 500" in caplog.text
    # Best effort: the cursor moved, the record is not sent again.
    assert n.tick() == 0 and len(send.calls) == 2


def test_an_error_in_the_pass_never_reaches_the_host(hive, key, monkeypatch, caplog):
    n = started(hive, key, Sender())
    monkeypatch.setattr(n, "_new_records", lambda: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="dispatcher.push"):
        assert n.tick() == 0
    assert "pass failed" in caplog.text


def test_a_missing_key_turns_push_off_and_logs_once(hive, tmp_path, caplog):
    send = Sender()
    missing = str(tmp_path / "none.pem")
    n = notifier(hive, missing, send)
    push.add(hive, sub("a"))
    note(hive)
    with caplog.at_level(logging.WARNING, logger="dispatcher.push"):
        assert n.tick() == 0
        assert n.tick() == 0
    assert caplog.text.count("push is off") == 1
    assert send.calls == []
    assert not Path(push.cursor_path(hive)).exists()


def test_a_partial_line_waits_for_its_end(hive, key):
    send = Sender()
    n = started(hive, key, send)
    path = notifications.notifications_path(hive)
    line = json.dumps({"id": "n-1", "kind": "blocked", "at": "x", "task": "t-1",
                       "title": "t-1 blocked", "url": "/tasks/t-1"})
    with open(path, "a") as fh:
        fh.write(line[:20])
    assert n.tick() == 0
    with open(path, "a") as fh:
        fh.write(line[20:] + "\n")
    assert n.tick() == 1
    assert send.calls[0][1]["id"] == "n-1"


def test_a_bad_line_is_skipped(hive, key, caplog):
    send = Sender()
    n = started(hive, key, send)
    with open(notifications.notifications_path(hive), "a") as fh:
        fh.write("not json\n")
    note(hive)
    with caplog.at_level(logging.WARNING, logger="dispatcher.push"):
        assert n.tick() == 1
    assert "notifications.jsonl" in caplog.text


def test_a_shorter_file_resets_the_cursor_to_its_end(hive, key, caplog):
    send = Sender()
    n = started(hive, key, send)
    Path(push.cursor_path(hive)).write_text(json.dumps({"offset": 10_000}))
    note(hive)
    with caplog.at_level(logging.WARNING, logger="dispatcher.push"):
        assert n.tick() == 0
    assert "shorter" in caplog.text
    note(hive, task="t-2")
    assert n.tick() == 1


def test_no_subscription_sends_nothing_and_moves_on(hive, key):
    send = Sender()
    n = started(hive, key, send)
    push.remove(hive, sub("a")["endpoint"])
    note(hive)
    assert n.tick() == 0
    push.add(hive, sub("a"))
    assert n.tick() == 0 and send.calls == []


def test_run_ticks_until_stopped(hive, key):
    import threading

    stopping = threading.Event()
    n = notifier(hive, key, Sender())
    calls = []
    n.tick = lambda: (calls.append(1), stopping.set())  # type: ignore[method-assign]
    n.run(stopping, interval=0)
    assert calls == [1]


def test_an_unreadable_store_is_no_subscriptions(hive, caplog):
    Path(push.subscriptions_path(hive)).write_text("{broken")
    with caplog.at_level(logging.WARNING, logger="dispatcher.push"):
        assert push.read_subscriptions(hive) == []
    assert "do not read" in caplog.text


def test_webpush_send_maps_a_push_service_answer(monkeypatch):
    """The real send's mapping, with a stand-in `pywebpush` module."""
    import sys
    import types

    class WebPushException(Exception):
        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    seen = {}

    def webpush(**kw):
        seen.update(kw)
        raise WebPushException("gone", response=types.SimpleNamespace(status_code=410))

    fake = types.SimpleNamespace(WebPushException=WebPushException, webpush=webpush)
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    status = push.webpush_send(sub("a"), "{}", key_path="/k", subject="mailto:x@y",
                               ttl=60, headers={"Urgency": "high"})
    assert status == 410
    assert seen["vapid_claims"] == {"sub": "mailto:x@y"}
    assert seen["vapid_private_key"] == "/k"
    assert seen["subscription_info"]["endpoint"] == sub("a")["endpoint"]


# -- the settings (ADR 237) ---------------------------------------------------

QUIET = {"start": "22:00", "end": "07:00", "zone": "UTC"}


class Wall:
    def __init__(self, hour: int, minute: int = 0) -> None:
        self.at = dt.datetime(2026, 10, 21, hour, minute, tzinfo=dt.timezone.utc)

    def __call__(self) -> dt.datetime:
        return self.at


def write_settings(hive, text: str) -> None:
    Path(push.settings_path(hive)).write_text(text)


def test_no_file_is_the_defaults(hive):
    settings, problems = push.read_settings(hive)
    assert problems == []
    assert settings == {"kinds": {k: on for k, (_, on) in notifications.KINDS.items()},
                        "quiet_hours": None}
    assert settings["kinds"]["awaiting_review"] is True
    assert settings["kinds"]["phase_failed"] is False


def test_an_unreadable_file_is_the_defaults_and_a_problem(hive):
    write_settings(hive, "{not json")
    settings, problems = push.read_settings(hive)
    assert settings == push.default_settings()
    assert len(problems) == 1


def test_bad_parts_take_their_default_unknown_kinds_are_ignored(hive):
    write_settings(hive, json.dumps({
        "kinds": {"awaiting_review": "yes", "merged": False, "gone_kind": True},
        "quiet_hours": {"start": "7:00", "end": "08:00", "zone": "UTC"},
    }))
    settings, problems = push.read_settings(hive)
    assert settings["kinds"]["awaiting_review"] is True  # default
    assert settings["kinds"]["merged"] is False
    assert "gone_kind" not in settings["kinds"]
    assert settings["quiet_hours"] is None
    assert len(problems) == 2  # the kind and the window; the unknown kind is quiet


def test_a_kind_the_file_does_not_name_takes_its_default(hive):
    write_settings(hive, json.dumps({"kinds": {"phase_failed": True}}))
    settings, problems = push.read_settings(hive)
    assert problems == []
    assert settings["kinds"]["phase_failed"] is True
    assert settings["kinds"]["conflict"] == notifications.KINDS["conflict"][1]


def test_an_update_merges_and_is_private(hive):
    push.update_settings(hive, {"kinds": {"phase_failed": True}})
    out = push.update_settings(hive, {"kinds": {"merged": False}, "quiet_hours": QUIET})
    assert out["kinds"]["phase_failed"] is True and out["kinds"]["merged"] is False
    assert out["quiet_hours"] == QUIET
    assert push.update_settings(hive, {"quiet_hours": None})["quiet_hours"] is None
    assert push.read_settings(hive)[0]["kinds"]["phase_failed"] is True
    assert Path(push.settings_path(hive)).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("body", [
    [], {}, {"x": 1}, {"kinds": []}, {"kinds": {"nope": True}},
    {"kinds": {"merged": "yes"}}, {"kinds": {"merged": 1}},
    {"quiet_hours": "22:00"},
    {"quiet_hours": {**QUIET, "start": "7:00"}},
    {"quiet_hours": {**QUIET, "start": "24:00"}},
    {"quiet_hours": {**QUIET, "end": "07:60"}},
    {"quiet_hours": {**QUIET, "end": "22:00"}},
    {"quiet_hours": {**QUIET, "zone": "Mars/Base"}},
    {"quiet_hours": {**QUIET, "zone": "A" * 65}},
    {"quiet_hours": {**QUIET, "zone": ""}},
    {"quiet_hours": {**QUIET, "x": 1}},
    {"quiet_hours": {"start": "22:00", "end": "07:00"}},
])
def test_a_bad_body_is_refused_and_nothing_is_stored(hive, body):
    with pytest.raises(push.SettingsRefused):
        push.update_settings(hive, body)
    assert not Path(push.settings_path(hive)).exists()


@pytest.mark.parametrize("hour, minute, quiet", [
    (21, 59, False), (22, 0, True), (3, 0, True), (6, 59, True), (7, 0, False),
])
def test_a_window_crossing_midnight(hour, minute, quiet):
    assert push.in_quiet_hours(QUIET, Wall(hour, minute)()) is quiet


def test_a_same_day_window_and_no_window():
    window = {"start": "13:00", "end": "14:00", "zone": "UTC"}
    assert push.in_quiet_hours(window, Wall(13, 0)()) is True
    assert push.in_quiet_hours(window, Wall(14, 0)()) is False
    assert push.in_quiet_hours(None, Wall(13, 0)()) is False


def test_a_window_is_read_in_its_zone():
    try:
        zoneinfo.ZoneInfo("America/New_York")
    except (zoneinfo.ZoneInfoNotFoundError, OSError):
        pytest.skip("no tz database here")
    window = {"start": "22:00", "end": "07:00", "zone": "America/New_York"}
    assert push.in_quiet_hours(window, Wall(3, 0)()) is True   # 23:00 the evening before
    assert push.in_quiet_hours(window, Wall(15, 0)()) is False  # 11:00


def test_a_kind_turned_on_is_pushed_and_one_turned_off_is_skipped(hive, key):
    send = Sender()
    n = started(hive, key, send)
    push.update_settings(hive, {"kinds": {"phase_failed": True, "awaiting_review": False}})
    note(hive, "phase_failed")
    note(hive, "awaiting_review")
    assert n.tick() == 1
    assert [c[1]["kind"] for c in send.calls] == ["phase_failed"]
    # The cursor moved past the skipped one: turning the kind back on pushes nothing.
    push.update_settings(hive, {"kinds": {"awaiting_review": True}})
    assert n.tick() == 0


def test_quiet_hours_hold_the_pushes_and_send_them_when_they_end(hive, key):
    wall = Wall(23)
    send = Sender()
    n = started(hive, key, send, wall_clock=wall)
    push.add(hive, sub("b"))
    push.update_settings(hive, {"quiet_hours": QUIET})
    note(hive, task="t-1")
    note(hive, task="t-2")
    note(hive, "merged", task="t-3")
    inbox = Path(notifications.notifications_path(hive)).read_bytes()
    cursor = Path(push.cursor_path(hive)).read_text()
    assert n.tick() == 0
    assert send.calls == [] and Path(push.cursor_path(hive)).read_text() == cursor
    wall.at = wall.at.replace(day=22, hour=7)
    assert n.tick() == 4  # two pushes, two subscriptions
    by_tag = {c[1]["tag"]: c[1]["title"] for c in send.calls}
    assert by_tag == {"kind:awaiting_review": "2 tasks awaiting review",
                      "task:t-3": "t-3 merged"}
    assert {c[0] for c in send.calls} == {sub("a")["endpoint"], sub("b")["endpoint"]}
    assert Path(notifications.notifications_path(hive)).read_bytes() == inbox
