"""The push notifier (ADR NNN): the cursor, the kinds, the tag, the grouping, the drop.

The send is a stub: nothing here imports pywebpush or reaches the network.
"""
import base64
import json
import logging
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
