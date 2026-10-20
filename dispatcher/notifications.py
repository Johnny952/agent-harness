"""The notification record: one line per thing that wants the person (ADR 221).

Step 1 of `docs/plans/notifications.md`. Whoever writes the state change also
appends a line here: the board module (`tasks`), which writes every task
file, and the dispatcher, for its own state. Nobody infers a notification
from the events log afterwards; a kind is added to `KINDS`, with its writer,
or it does not exist.

The file is `<hive root>/notifications.jsonl`, beside the board and the
locks, append-only like a task's `comments.jsonl`. A line is
`{"id", "kind", "at", "task" | "thread" | "account", "title", "url"}`: what
it is about, a short title, and the console url that opens the item. The
title names the item and what happened to it, never a diff, a command's text
or a handoff's content (the plan's *Decisions* 4): it is what a push will
show on a locked phone.

An append never raises into its caller. The state change it rides on is the
record of truth, and a notification lost to a full disk is a warning in the
log, not a task file left half written.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import secrets
import urllib.parse

from dispatcher import context_transfer

logger = logging.getLogger(__name__)

NOTIFICATIONS_FILE = "notifications.jsonl"

NEEDS_PERSON = "needs the person"
INFORMS = "informs"

#: The plan's kinds table, one kind per row, in its order: each kind's group
#: and whether it is on by default (step 5's settings read the default).
KINDS: dict[str, tuple[str, bool]] = {
    "awaiting_review": (NEEDS_PERSON, True),
    "conflict": (NEEDS_PERSON, True),
    "command_request": (NEEDS_PERSON, True),
    "blocked": (NEEDS_PERSON, True),
    # Merged, and an epic closed.
    "merged": (INFORMS, True),
    # Pool dry, and an account available again.
    "pool": (INFORMS, True),
    "phase_failed": (INFORMS, False),
    "turn_finished": (INFORMS, False),
}

#: What a line is about: exactly one of these, a string.
SUBJECTS = ("task", "thread", "account")

#: A title is one line a lock screen shows. Bounded so a whole line stays well
#: under the size one `O_APPEND` write keeps whole against another writer.
MAX_TITLE_CHARS = 200

#: The keys every line carries, besides its one subject.
RECORD_KEYS = ("id", "kind", "at", "title", "url")


def notifications_path(hive_dir: str) -> str:
    """`<hive root>/notifications.jsonl`, a sibling of the tasks dir and the board."""
    return os.path.join(context_transfer.hive_root(hive_dir), NOTIFICATIONS_FILE)


def new_id() -> str:
    """`n-<UTC time>Z-<4 hex>`, the queue's action ids' shape: ids sort as written."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"n-{stamp}Z-{secrets.token_hex(2)}"


def task_url(task_id: str) -> str:
    """The console's task detail, the screen that opens a task of any project."""
    return f"/tasks/{urllib.parse.quote(task_id, safe='')}"


POOL_URL = "/pool"


def task_title(task_id: str, what: str, title: object = None) -> str:
    """`<id> <what>`, and the task's own title after a colon when it has one."""
    head = f"{task_id} {what}"
    if isinstance(title, str) and title.strip():
        return f"{head}: {' '.join(title.split())}"
    return head


def append(
    hive_dir: str,
    kind: str,
    *,
    title: str,
    url: str,
    task: str | None = None,
    thread: str | None = None,
    account: str | None = None,
) -> dict | None:
    """Append one notification; the record written, or None when it was not.

    `kind` is one of `KINDS` and exactly one of `task`, `thread` and
    `account` is given: a caller that breaks either is a bug, and raises
    `ValueError`. Past that nothing raises: an `OSError` is logged and the
    caller's state change stands. The line is one `write` on an `O_APPEND`
    file, which keeps two writers' lines whole without a lock, so a writer
    that holds a task's lock appends under it without taking another.
    """
    if kind not in KINDS:
        raise ValueError(f"notification kind {kind!r} is not one of {', '.join(KINDS)}")
    subjects = {k: v for k, v in (("task", task), ("thread", thread), ("account", account))
                if v is not None}
    if len(subjects) != 1:
        raise ValueError(f"a notification is about one task, thread or account, not {subjects}")
    title = " ".join(str(title).split())
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1] + "…"
    record = {
        "id": new_id(),
        "kind": kind,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        **subjects,
        "title": title,
        "url": url,
    }
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    path = notifications_path(hive_dir)
    try:
        context_transfer._makedirs_readable(
            context_transfer.hive_root(hive_dir), os.path.dirname(path)
        )
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("notification %s about %s not written: %s", kind, subjects, exc)
        return None
    return record


def notify_task(hive_dir: str, kind: str, task_id: str, what: str, title: object = None) -> None:
    """`append` about a task, titled by `task_title` and opening its detail."""
    append(
        hive_dir, kind, task=task_id,
        title=task_title(task_id, what, title), url=task_url(task_id),
    )


def parse_line(line: bytes) -> tuple[dict | None, str | None]:
    """One non-blank line: `(record, None)`, or `(None, problem)`.

    `parse_comment_line`'s rule for this file: a record is a JSON object with
    a string for each of `RECORD_KEYS`, a `kind` in `KINDS`, and one string
    subject. Unknown keys are kept, so a later step can add one; a kind this
    reader does not know is a problem rather than a guess at its group.
    """
    try:
        value = json.loads(line)
    except ValueError as exc:
        return None, f"not a JSON line ({exc}), skipped"
    if not isinstance(value, dict):
        return None, f"not a JSON object but a {type(value).__name__}, skipped"
    missing = [key for key in RECORD_KEYS if not isinstance(value.get(key), str)]
    if missing:
        return None, f"no string {', '.join(missing)}, skipped"
    if value["kind"] not in KINDS:
        return None, f"kind {value['kind']!r} is not one this reader knows, skipped"
    subjects = [key for key in SUBJECTS if value.get(key) is not None]
    if len(subjects) != 1 or not isinstance(value[subjects[0]], str):
        return None, "not about exactly one task, thread or account, skipped"
    return value, None
