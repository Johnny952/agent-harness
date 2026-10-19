"""The console's thread records, `<hive root>/threads/<id>.json` (`docs/decisions.md` ADR 170).

A sibling of `tasks.py`: every write is `context_transfer._write_atomic` under
the hive's write lock (`tasks.task_lock`) with a name no task id can take. The
main thread is the id `main`, so a second one cannot exist.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets

from dispatcher import actions, context_transfer
from dispatcher import tasks as task_store
from dispatcher.config import EFFORT_LEVELS, Config, refresh_primary

THREADS_DIR_NAME = "threads"
CONVERSATIONS_DIR_NAME = "conversations"
MAIN_ID = "main"
MAIN_TITLE = "Main thread"
STATUSES = ("open", "archived")

_ORDINARY_ID = re.compile(r"th-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")


class ThreadRefused(ValueError):
    """A request the store refuses; the message is the reason."""


class NoThreadError(ThreadRefused):
    pass


class ThreadExistsError(ThreadRefused):
    pass


class UnreadableThreadError(ThreadRefused):
    pass


def threads_root(hive_dir: str) -> str:
    return os.path.join(context_transfer.hive_root(hive_dir), THREADS_DIR_NAME)


def _known_id(thread_id: object) -> bool:
    return isinstance(thread_id, str) and (
        thread_id == MAIN_ID or _ORDINARY_ID.fullmatch(thread_id) is not None
    )


def thread_path(hive_dir: str, thread_id: str) -> str:
    """`NoThreadError` for an id the module did not make."""
    if not _known_id(thread_id):
        raise NoThreadError(f"no thread {thread_id!r}")
    return os.path.join(threads_root(hive_dir), f"{thread_id}.json")


def _now(now: dt.datetime | None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).isoformat()


def _lock(hive_dir: str, thread_id: str):
    return task_store.task_lock(hive_dir, f"_thread-{thread_id}")


def _write(hive_dir: str, record: dict) -> None:
    root = threads_root(hive_dir)
    context_transfer._makedirs_readable(context_transfer.hive_root(hive_dir), root)
    context_transfer._write_atomic(
        os.path.join(root, f"{record['id']}.json"), json.dumps(record, indent=2) + "\n"
    )


def _check_triple(cfg: Config, account: str, model: str, effort: str) -> None:
    try:
        models = cfg.thread_models_for(account)
    except KeyError:
        known = ", ".join(a.name for a in cfg.accounts)
        raise ThreadRefused(f"unknown account {account!r} (have: {known})") from None
    if model not in models:
        raise ThreadRefused(
            f"model {model!r} is not in account {account}'s models ({', '.join(models)})"
        )
    if effort not in EFFORT_LEVELS:
        raise ThreadRefused(f"effort must be one of: {', '.join(EFFORT_LEVELS)}")


def _default_account(cfg: Config) -> str:
    # Re-read, as the picker does: the board service loads its config once,
    # and the console moves the primary while it runs (ADR 176, ADR 178).
    refresh_primary(cfg)
    return cfg.primary_account or cfg.accounts[0].name


def _title(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ThreadRefused("title must be a non-empty string")
    value = value.strip()
    if len(value) > task_store.MAX_TITLE_CHARS:
        raise ThreadRefused(f"title is longer than {task_store.MAX_TITLE_CHARS} characters")
    return value


def create(
    hive_dir: str, cfg: Config, *, title: str | None = None, project: str | None = None,
    account: str | None = None, model: str | None = None, effort: str | None = None,
    task: str | None = None, main: bool = False, now: dt.datetime | None = None,
) -> dict:
    """Make a thread and return its record. `ThreadRefused` when a value is not valid."""
    if main:
        if project is not None or task is not None:
            raise ThreadRefused("the main thread takes no project and no task")
        title = MAIN_TITLE if title is None else title
    title = _title(title)
    if project is not None:
        problems = actions._slug_problem("project", project)
        if problems:
            raise ThreadRefused(problems[0])
    if task is not None and (
        not _known_task(task)
        or not os.path.isfile(context_transfer.task_file_path(hive_dir, task))
    ):
        raise ThreadRefused(f"no task {task!r}")
    account = account if account is not None else _default_account(cfg)
    if model is None:
        try:
            models = cfg.thread_models_for(account)
        except KeyError:
            models = []
        model = cfg.default_model if cfg.default_model in models else (models[0] if models else "")
    effort = effort if effort is not None else cfg.default_effort
    _check_triple(cfg, account, model, effort)
    created = _now(now)
    thread_id = MAIN_ID if main else (
        f"th-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    )
    record = {
        "id": thread_id, "title": title, "project": project, "account": account,
        "model": model, "effort": effort,
        "segments": [
            {"session_id": None, "account": account, "model": model, "effort": effort,
             "from": created}
        ],
        "created": created, "status": "open", "task": task,
    }
    with _lock(hive_dir, thread_id):
        if os.path.exists(thread_path(hive_dir, thread_id)):
            if main:
                raise ThreadExistsError("the main thread already exists")
            raise ThreadRefused(f"thread {thread_id} already exists")
        _write(hive_dir, record)
    return record


def _known_task(task: object) -> bool:
    return isinstance(task, str) and context_transfer._is_single_path_component(task)


def _load(path: str, thread_id: str) -> dict:
    try:
        with open(path) as f:
            record = json.load(f)
    except FileNotFoundError:
        raise NoThreadError(f"no thread {thread_id!r}") from None
    except ValueError as exc:
        raise UnreadableThreadError(f"thread {thread_id} does not parse: {exc}") from None
    if not isinstance(record, dict):
        raise UnreadableThreadError(f"thread {thread_id} is not a JSON object")
    return record


def read_thread(hive_dir: str, thread_id: str) -> dict:
    return _load(thread_path(hive_dir, thread_id), thread_id)


def read_threads(hive_dir: str) -> tuple[list[dict], list[str]]:
    """Every record, the main thread first and then by `created`, and a warning
    per file that does not parse or is not a JSON object."""
    root = threads_root(hive_dir)
    try:
        names = sorted(n for n in os.listdir(root) if n.endswith(".json"))
    except FileNotFoundError:
        return [], []
    records: list[dict] = []
    warnings: list[str] = []
    for name in names:
        try:
            with open(os.path.join(root, name)) as f:
                record = json.load(f)
            if not isinstance(record, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as exc:
            warnings.append(f"threads/{name}: {exc}")
            continue
        records.append(record)
    records.sort(key=lambda r: (r.get("id") != MAIN_ID, str(r.get("created", ""))))
    return records, warnings


def update(
    hive_dir: str, cfg: Config, thread_id: str, *, title: str | None = None,
    account: str | None = None, model: str | None = None, effort: str | None = None,
    status: str | None = None, session_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Change a thread. A changed account, model or effort opens one segment
    that carries the last segment's `session_id`; title and status open none.
    `session_id` is the worker's (ADR 173, 6): it is set on the last segment,
    after any segment this call opened, and opens none."""
    path = thread_path(hive_dir, thread_id)
    with _lock(hive_dir, thread_id):
        record = _load(path, thread_id)
        if title is not None:
            record["title"] = _title(title)
        if status is not None:
            if status not in STATUSES:
                raise ThreadRefused(f"status must be one of: {', '.join(STATUSES)}")
            if thread_id == MAIN_ID and status != "open":
                raise ThreadRefused("the main thread cannot be archived")
            record["status"] = status
        new = {
            "account": account if account is not None else record["account"],
            "model": model if model is not None else record["model"],
            "effort": effort if effort is not None else record["effort"],
        }
        if any(new[k] != record[k] for k in new):
            _check_triple(cfg, new["account"], new["model"], new["effort"])
            record.update(new)
            last = record["segments"][-1]
            record["segments"].append(
                {"session_id": last.get("session_id"), **new, "from": _now(now)}
            )
        if session_id is not None:
            record["segments"][-1]["session_id"] = session_id
        _write(hive_dir, record)
    return record


def conversation_path(hive_dir: str, thread_id: str) -> str:
    """`NoThreadError` for an id the module did not make."""
    if not _known_id(thread_id):
        raise NoThreadError(f"no thread {thread_id!r}")
    return os.path.join(
        context_transfer.hive_root(hive_dir), CONVERSATIONS_DIR_NAME, f"{thread_id}.jsonl"
    )


def append_conversation(hive_dir: str, thread_id: str, line: dict | str) -> None:
    """Append one line to the thread's log and flush it at once (ADR 173, 4).
    Append-only: nothing here reads, rewrites or trims the file."""
    path = conversation_path(hive_dir, thread_id)
    context_transfer._makedirs_readable(
        context_transfer.hive_root(hive_dir), os.path.dirname(path)
    )
    text = line if isinstance(line, str) else json.dumps(line)
    with open(path, "a") as f:
        f.write(text.rstrip("\n") + "\n")
        f.flush()
