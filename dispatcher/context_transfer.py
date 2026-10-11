from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


class _TaskDumper(yaml.SafeDumper):
    """SafeDumper that emits multi-line strings as block scalars.

    A task description is usually several lines; safe_dump's default for
    those is a single-quoted scalar with the newlines folded, which makes
    the task file unreadable for the human who wrote the description and
    for anyone debugging a run. Subclassed rather than registered on
    yaml.SafeDumper so the other safe_dump callers in this repo keep the
    stock behaviour. Fidelity is unaffected either way: the emitter falls
    back to a quoted style on its own whenever a block scalar can't
    represent the value (trailing spaces, \\r, and similar).
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_TaskDumper.add_representer(str, _represent_str)


class LockHeldError(RuntimeError):
    """Raised when acquire_lock is asked to take a task whose lock is live under a different owner."""

    def __init__(self, task_id: str, owner: str) -> None:
        super().__init__(f"task {task_id} is locked by {owner}")
        self.task_id = task_id
        self.owner = owner


@dataclasses.dataclass
class TaskFile:
    task_id: str
    status: str
    owner: str | None
    depends_on: list[str]
    heartbeat: str | None
    body: str
    # The task as the operator stated it, kept in the frontmatter rather
    # than in `body`: handoff() appends each phase summary to `body`, so a
    # description written there would be duplicated on every re-run of the
    # same task id, and the roles could no longer tell the original ask
    # apart from what previous roles said about it. Last field (and last in
    # the frontmatter) so existing TaskFile(...) constructions still work.
    description: str | None = None
    # The uuid of the Vibe Kanban issue this task shows up as, when there is
    # a board at all. It lives here rather than in a dispatcher-side map
    # because the task file is what survives a restart, and because the id is
    # server-assigned: the harness can't derive it from the task id.
    kanban_issue_id: str | None = None
    # The debt entries this task's implementation says it resolved. Kept here
    # for the same reason as the issue id: the merge that closes their cards
    # can happen in a later process — `merge-task` by hand, days after the
    # cycle — and by then the handoff that claimed them is only prose.
    resolved_debt: list[str] = dataclasses.field(default_factory=list)


def hive_root(hive_dir: str) -> str:
    """The directory the task files and the learnings share.

    Derived, not configured, for the reason `learnings.root_dir` gives: both
    sides of the mount have to agree, and a second config key is a second
    thing that can disagree. The dispatcher hands this to a phase as an extra
    allowed directory — a role is told to read its task file and to file a
    learnings entry, and both sit outside the worktree, where the file tools
    refuse them.
    """
    return os.path.dirname(os.path.normpath(hive_dir))


def task_file_path(hive_dir: str, task_id: str) -> str:
    return os.path.join(hive_dir, f"{task_id}.md")


def scratch_dir(hive_dir: str, task_id: str) -> str:
    """Where a phase puts detail that only this task's later rounds need.

    Per-round working notes — review findings, test logs, a scratch plan —
    are too long to hand off and too short-lived for the repo, so they go
    here and the handoff cites the path. It sits beside the task file rather
    than inside the project: a reviewing role's checkout is rebuilt every
    round, so anything written there is gone by the next one.

    Safe as a sibling of `<task_id>.md` because `list_task_ids` only counts
    `.md` files, and `.hive/` is gitignored.
    """
    return os.path.join(hive_dir, task_id)


def ensure_scratch_dir(hive_dir: str, task_id: str) -> str:
    path = scratch_dir(hive_dir, task_id)
    os.makedirs(path, exist_ok=True)
    return path


#: Where the dispatcher keeps its own copy of what a phase returned, under the
#: task's scratch dir. Namespaced into a subdirectory rather than dropped in
#: beside the roles' notes: the scratch dir is handed to every phase as a
#: writable directory, and a role that decides to call its own file
#: `revisor.json` must not be able to overwrite the dispatcher's record of what
#: the revisor said.
_HANDOFF_SUBDIR = "handoffs"


def handoff_path(hive_dir: str, task_id: str, role: str) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _HANDOFF_SUBDIR, f"{role}.json")


def save_handoff(
    hive_dir: str,
    task_id: str,
    role: str,
    payload: dict | None,
    round_num: int | None = None,
) -> str:
    """Keep the structured return of a phase, not just its prose rendering.

    The handoff a role writes is parsed into a dict, rendered into the task
    file as prose, and then lost with the process. Everything the cycle does
    with it afterwards — the debt a task declared, the debt a revisor accepted,
    the entries a merge has to close — needs the dict and cannot be recovered
    from the prose. So the cycle could only ever do that work in the one run
    that held all of it in memory, and a task driven phase by phase, by hand,
    silently got none of it.

    One file per role, overwritten each round, is enough because of how the
    cycle terminates: it stops looping when the revisor approves, so the last
    file a role left is the round that was approved, which is exactly the round
    whose debt is real. `round` is kept in the envelope anyway — for a reader
    trying to work out what happened, the round number is the difference
    between a task that was approved first time and one that took three.

    Written even when `payload` is None, so that a re-run whose handoff failed
    to parse clears the previous round's file rather than leaving it standing
    as if it were this round's answer.
    """
    path = handoff_path(hive_dir, task_id, role)
    envelope = {
        "role": role,
        "round": round_num,
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "handoff": payload,
    }
    _write_atomic(path, json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    return path


def list_handoff_roles(hive_dir: str, task_id: str) -> list[str]:
    """The roles that left a record on this task, sorted, empty when none did.

    `list_task_ids`' shape, including its `os.path.isdir` guard: a task that
    predates `save_handoff`, or one no phase has finished yet, has no handoffs
    directory at all and that is a task with no records rather than damage.

    Sorted by name and not ordered by the dispatcher's role list, which is not
    this function's business. A file named after a role this harness does not
    run is still a file, and a reader that walks these has to be told about it
    rather than have it disappear.
    """
    directory = os.path.join(scratch_dir(hive_dir, task_id), _HANDOFF_SUBDIR)
    if not os.path.isdir(directory):
        return []
    return sorted(Path(f).stem for f in os.listdir(directory) if f.endswith(".json"))


def read_handoff_envelope(hive_dir: str, task_id: str, role: str) -> dict:
    """The whole envelope `save_handoff` wrote — all four keys — or an exception.

    For the reader that has to tell an operator *why* a record could not be
    read, which `read_handoff` cannot: it answers `None` for a file that is
    missing, a file that will not parse and a file holding the wrong shape, and
    those are three different sentences on a screen. `observability/api/app.py`
    is that reader and names the path in a warning.

    Raises `OSError` for a file that will not open and `ValueError` for one
    that will not parse or is not a JSON object — `json.JSONDecodeError` is a
    `ValueError` already, which is why a caller catches the family and not the
    subclass (`docs/learnings/a-lookup-that-never-raises-catches-valueerror.md`).
    """
    with open(handoff_path(hive_dir, task_id, role)) as fh:
        envelope = json.load(fh)
    if not isinstance(envelope, dict):
        raise ValueError(
            f"a handoff is a JSON object of role, round, saved_at and handoff, "
            f"not a {type(envelope).__name__}"
        )
    return envelope


def read_handoff(hive_dir: str, task_id: str, role: str) -> dict | None:
    """What that role returned, or None if this task has no record of it.

    Every caller reads a handoff to ask what it declared, and every one of
    those questions answers "nothing" for a payload that is missing, empty or
    malformed — `debt.declarations`, `debt.rulings` and `debt.resolved` all
    take `dict | None` for that reason. So a damaged file is not worth an
    exception here: the paths that read this are repair paths, and a task
    whose record is unreadable should still be closeable by hand.

    Written in terms of `read_handoff_envelope` so there is one definition of
    where a handoff lives and what shape it has; the swallow is this function's
    contract and not the module's.
    """
    try:
        envelope = read_handoff_envelope(hive_dir, task_id, role)
    except (OSError, ValueError):
        return None
    payload = envelope.get("handoff")
    return payload if isinstance(payload, dict) else None


#: Where the dispatcher keeps what each `claude` call it made for this task
#: cost (`docs/decisions.md` ADR 53). Namespaced into its own subdirectory for
#: the reason `_HANDOFF_SUBDIR` gives, and a sibling of it rather than a file
#: inside it: `list_handoff_roles` reads every `.json` in that directory as a
#: role, and this log is the dispatcher's own observation, not a role's return.
_USAGE_SUBDIR = "usage"
_USAGE_FILE = "calls.jsonl"


def usage_log_path(hive_dir: str, task_id: str) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _USAGE_SUBDIR, _USAGE_FILE)


def append_usage(hive_dir: str, task_id: str, record: dict) -> str:
    """Add one line to this task's usage log, and return the file it went in.

    Appended with `O_APPEND` rather than through `_write_atomic`, which is the
    one place in this module that difference is deliberate. That helper exists
    for a JSON *document* another process may read while it is being replaced;
    this is a log, every line of it is final once written, and the task lock
    already makes the dispatcher its only writer — so re-serialising the whole
    file per `claude` call would buy nothing and lose the series as it grows.
    The reader's half of that contract: skip a line that will not parse, and
    do not assume the last line is complete, rather than calling the file
    damaged (`docs/learnings/atomic-writes-copy-state-machine.md` is about the
    document case and does not fire here).

    No `chmod` either: `open(..., "a")` creates at the process umask, where
    `_write_atomic` has to undo `NamedTemporaryFile`'s 0600.

    Raises `OSError` to its caller. Swallowing belongs at the call site, where
    the dispatcher knows this is bookkeeping on a path that may already be
    failing and must never be what fails a phase.
    """
    path = usage_log_path(hive_dir, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return path


def list_task_ids(hive_dir: str) -> list[str]:
    if not os.path.isdir(hive_dir):
        return []
    return [Path(f).stem for f in os.listdir(hive_dir) if f.endswith(".md")]


def _as_heartbeat_str(value: object) -> str | None:
    """The heartbeat as the dataclass declares it, whatever YAML made of it.

    `write_task_file` quotes the timestamp, so a card this harness wrote reads
    back as a `str`. A hand-edited card does not, and PyYAML resolves an
    unquoted ISO timestamp to a `datetime` — which then reaches
    `is_lock_expired` as `fromisoformat: argument must be str`. A hand-edited
    card is exactly what somebody is holding when they go looking at locks, so
    the coercion happens here, once, rather than at every reader.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dt.datetime):
        # No offset means UTC: every heartbeat this harness writes is, and a
        # naive one compared against an aware `now` raises rather than expires.
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    # Anything else is not a timestamp, and a field that cannot be compared is
    # worth less than no field: an unheld card is takeable, a card held by
    # garbage would be held forever.
    return None


def read_task_file(path: str) -> TaskFile:
    text = Path(path).read_text()
    _, fm_text, body = text.split(_FRONTMATTER_DELIM, 2)
    fm = yaml.safe_load(fm_text) or {}
    return TaskFile(
        task_id=fm["task_id"],
        status=fm["status"],
        owner=fm.get("owner"),
        depends_on=fm.get("depends_on", []),
        heartbeat=_as_heartbeat_str(fm.get("heartbeat")),
        body=body.lstrip("\n"),
        # .get, not [...]: task files written before descriptions existed
        # have no such key and must stay readable.
        description=fm.get("description"),
        kanban_issue_id=fm.get("kanban_issue_id"),
        resolved_debt=fm.get("resolved_debt") or [],
    )


def has_phase_section(hive_dir: str, task_id: str, role: str) -> bool:
    """The dispatcher's own record that this role's phase returned on this task.

    `handoff()` appends one `## <label>` block per phase that finished, and the
    phase loop only builds that label after `run_phase` returned something — so
    a section is there because a phase came back, and it outlives the worktree,
    the container and the process the phase ran in. That makes it the local
    stand-in for "this role ran" that `learnings.drop_promoted` gates on
    (`docs/decisions.md` ADR 34, `docs/debt/T-012-D1.md`).

    The heading is matched in the parsed body rather than in the file's text,
    anchored, with exactly two hashes, and `auditor (round 2)` counts as well as
    `auditor` because `run-phase --round` labels a hand-resumed phase that way.

    A card that is missing or will not parse answers `False`: it is absence of
    evidence, and the one caller's `True` moves files. That is the exception
    `a-never-500-read-wraps-the-use-not-the-parse` leaves room for — the
    predicate's `False` already *means* "no evidence", so widening at the caller
    would only turn a damaged card into a crashed `merge-task`. Silent because
    this module has no logger; the caller logs what it kept.

    The tuple is the five that learning names, because `read_task_file` raises
    every one: no file (`OSError`), no `---` to split on (`ValueError`),
    frontmatter that does not scan (`yaml.YAMLError`), one that scans but is
    missing a key (`KeyError`), and one that scans into something that is not a
    mapping at all — `TODO write this up`, or a list of bullets — where
    `fm["task_id"]` subscripts a `str` or a `list` (`TypeError`). A hand-edited
    card is the normal state of the cycles `merge-task` runs on, so that last
    one is the realistic shape of a damaged card rather than a contrived one.
    `AttributeError` is not in it: `fm["task_id"]` is evaluated before any
    `fm.get`, so the subscript raises first for every non-mapping PyYAML returns.
    """
    try:
        body = read_task_file(task_file_path(hive_dir, task_id)).body
    except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError):
        return False
    heading = re.compile(
        rf"^## {re.escape(role)}(?: \(round \d+\))?$", re.MULTILINE
    )
    return heading.search(body) is not None


def _read_or_new(hive_dir: str, task_id: str) -> tuple[str, TaskFile]:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        return path, read_task_file(path)
    return path, TaskFile(
        task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body=""
    )


def _write_atomic(path: str, content: str) -> None:
    """Replace a file's contents in one step, readable by whoever reads it.

    Every file under `.hive/` is written while something else may be reading
    it — the heartbeat thread rewrites the card under the phase that is
    holding it, a later process reads a handoff the cycle is still adding to —
    so nothing here is written in place. The temp file goes in the same
    directory, because `os.replace` is only atomic within one filesystem, and
    carries a non-".md" suffix so a leftover from a crash is never picked up
    by `list_task_ids` as a task.
    """
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=parent, prefix=f".{Path(path).stem}.", suffix=".tmp", delete=False
    )
    try:
        with tmp:
            tmp.write(content)
            tmp.flush()
        # NamedTemporaryFile creates the file 0600, and os.replace keeps that
        # mode. Agent containers (possibly non-root) and the host operator
        # both need to read this file, so restore 0644 before the replace.
        # Not os.umask(): that's process-wide and would race the heartbeat
        # thread's own writes.
        os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.remove(tmp.name)
        except FileNotFoundError:
            pass
        raise


def write_task_file(path: str, task: TaskFile) -> None:
    fm = {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "depends_on": task.depends_on,
        "heartbeat": task.heartbeat,
    }
    if task.kanban_issue_id is not None:
        fm["kanban_issue_id"] = task.kanban_issue_id
    if task.resolved_debt:
        fm["resolved_debt"] = task.resolved_debt
    if task.description is not None:
        # Last key so the (multi-line) description sits next to the body,
        # with the short bookkeeping fields readable above it.
        fm["description"] = task.description
    dumped = yaml.dump(fm, Dumper=_TaskDumper, sort_keys=False, default_flow_style=False)
    _write_atomic(path, f"{_FRONTMATTER_DELIM}\n{dumped}{_FRONTMATTER_DELIM}\n\n{task.body}")


def set_description(hive_dir: str, task_id: str, description: str) -> None:
    """Store the operator's task description, creating the task file if needed.

    Everything else on an existing file is preserved: re-seeding a task
    that already has phase history in its body only replaces the ask.
    """
    path, task = _read_or_new(hive_dir, task_id)
    task.description = description
    write_task_file(path, task)


def read_description(hive_dir: str, task_id: str) -> str | None:
    """The stored description, or None when the task file doesn't exist yet."""
    return _read_or_new(hive_dir, task_id)[1].description


def set_kanban_issue_id(hive_dir: str, task_id: str, issue_id: str) -> None:
    """Point this task at a Vibe Kanban issue, creating the task file if needed.

    Like set_description, everything else on an existing file is preserved:
    attaching a board to a task that already ran only adds the id.
    """
    path, task = _read_or_new(hive_dir, task_id)
    task.kanban_issue_id = issue_id
    write_task_file(path, task)


def read_kanban_issue_id(hive_dir: str, task_id: str) -> str | None:
    """The issue this task mirrors, or None when it mirrors none."""
    return _read_or_new(hive_dir, task_id)[1].kanban_issue_id


def set_resolved_debt(hive_dir: str, task_id: str, entries: list[str]) -> None:
    """Record which debt entries this task's work resolved.

    Replaces rather than appends: a re-run's implementation is the current
    claim about what this task fixes, and the previous round's claim was
    about code that has since been rewritten.
    """
    path, task = _read_or_new(hive_dir, task_id)
    task.resolved_debt = list(entries)
    write_task_file(path, task)


def read_resolved_debt(hive_dir: str, task_id: str) -> list[str]:
    """The debt entries this task claims to have resolved, empty when none."""
    return _read_or_new(hive_dir, task_id)[1].resolved_debt


def acquire_lock(hive_dir: str, task_id: str, owner: str, ttl_seconds: int | None = None) -> TaskFile:
    path, task = _read_or_new(hive_dir, task_id)
    # Two dispatcher processes, or a stale retry, must not both drive the same
    # task. This is a read-then-write check, not a cross-process mutex — a
    # real mutual-exclusion guard (e.g. flock) is separate future work.
    if task.owner is not None and task.owner != owner:
        # A live holder always refreshes its heartbeat, so a foreign owner
        # with heartbeat None can only be a hand-edited or legacy file, not
        # an active dispatcher. Treat it as stale when the caller opted into
        # takeovers at all (ttl_seconds given) rather than locking the task
        # forever with no way to reap it.
        heartbeat_missing = task.heartbeat is None
        if ttl_seconds is None or not (heartbeat_missing or is_lock_expired(task, ttl_seconds)):
            raise LockHeldError(task_id, task.owner)
    task.owner = owner
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    if task.status == "pending":
        task.status = "in_progress"
    write_task_file(path, task)
    return task


def refresh_heartbeat(hive_dir: str, task_id: str) -> None:
    path = task_file_path(hive_dir, task_id)
    task = read_task_file(path)
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    write_task_file(path, task)


def is_lock_expired(task: TaskFile, ttl_seconds: int, now: dt.datetime | None = None) -> bool:
    if task.heartbeat is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    heartbeat_dt = dt.datetime.fromisoformat(task.heartbeat)
    return (now - heartbeat_dt).total_seconds() > ttl_seconds


def release_stale_lock(hive_dir: str, task_id: str) -> None:
    path = task_file_path(hive_dir, task_id)
    task = read_task_file(path)
    task.owner = None
    task.heartbeat = None
    write_task_file(path, task)


def handoff(
    hive_dir: str,
    task_id: str,
    new_status: str,
    body: str,
    depends_on: list[str] | None = None,
) -> None:
    path, task = _read_or_new(hive_dir, task_id)
    task.status = new_status
    task.owner = None
    task.heartbeat = None
    # Accumulate phase summaries rather than replacing task.body — the next
    # role needs the full trail of prior phases, not just the last one.
    task.body = f"{task.body}\n\n{body}" if task.body else body
    if depends_on is not None:
        task.depends_on = depends_on
    write_task_file(path, task)
