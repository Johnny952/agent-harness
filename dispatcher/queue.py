# dispatcher/queue.py
"""The queue: a list of tasks the owner confirmed, run one cycle each, to a limit.

`docs/plans/autonomy.md` *Decisions*, 3, task A-5, and `docs/decisions.md`
ADR 79, under charter C-4 (*Amended* 2026-10-15). A queue is the tasks listed
when it was confirmed and nothing else, run one at a time in an order that
follows `depends_on`:

- a task whose dependency is not merged yet waits, and the queue goes on with
  the next one that does not;
- a task that fails (blocked, held after it spent, split, interrupted) is not
  retried, and every task of the queue that depends on it is skipped with a
  comment;
- the limit is in measured tokens, read from each task's usage log; once the
  total reaches it no new phase or task starts and the queue stops with
  `limit_reached`;
- no account open is waited out for free, with the same re-probe the
  dispatcher already makes (`_recheck_cooling_accounts`);
- an automatic approve whose push was refused stops the queue, because every
  later task would build on a base the remote does not have.

The queue is a file in `state_dir/queues/`, rewritten atomically on every
change of state, so a kill at any point leaves it readable. A task is marked
`running` before its cycle starts; `--resume` finds it that way, records it
`interrupted` and never starts it again.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import re
import secrets
import tempfile
import time
from collections.abc import Callable

from dispatcher import context_transfer, project_docs, tasks
from dispatcher import dispatcher as core
from dispatcher.config import Config

logger = logging.getLogger(__name__)

#: Where queue files live, under `state_dir`.
QUEUE_SUBDIR = "queues"

#: Seconds between two looks for an open account while the queue waits.
WAIT_POLL_SECONDS = 300

#: How much of a task's goal its title shows on the confirmation screen.
TITLE_WIDTH = 72

#: A queue id as `new_queue_id` makes it; anything else is refused on resume.
_QUEUE_ID = re.compile(r"^q-\d{8}T\d{6}Z-[0-9a-f]{4}$")

# The states of a task in the queue file.
PENDING = "pending"
RUNNING = "running"
FINISHED = "finished"  # the auditor closed it; not merged (yet)
MERGED = "merged"
BLOCKED = "blocked"
HELD = "held"  # held after a phase of it spent: one cycle, not retried
SPLIT = "split"
SKIPPED = "skipped"
INTERRUPTED = "interrupted"  # found `running` on resume: never started again
STOPPED = "limit_reached"  # stopped by the limit before or inside its cycle

#: A task in one of these ended its one cycle without something to build on:
#: whatever depends on it is skipped.
FAILED = frozenset({BLOCKED, HELD, SPLIT, SKIPPED, INTERRUPTED})

# The states of the queue itself.
Q_RUNNING = "running"
Q_DONE = "done"
Q_WAITING = "waiting"  # what is left waits on a merge outside the queue
Q_LIMIT = "limit_reached"
Q_PUSH_REFUSED = "push_refused"


class QueueError(ValueError):
    """A queue that cannot be planned or resumed. The message says why."""


@dataclasses.dataclass
class Entry:
    """One task of the queue, as the file holds it."""

    task_id: str
    title: str
    depends_on: list[str]
    state: str = PENDING
    # Lines of the task's usage log before its cycle started: its spend is
    # what was written after them.
    usage_offset: int | None = None
    tokens: int = 0
    unmeasured: int = 0
    note: str = ""


@dataclasses.dataclass
class Queue:
    """A confirmed queue: what it runs, under which limit, and how far it got."""

    queue_id: str
    project: str
    limit: int
    entries: list[Entry]
    parallel: int = 1
    authorized: bool = True
    status: str = Q_RUNNING
    created_at: str = ""

    def entry(self, task_id: str) -> Entry:
        return next(e for e in self.entries if e.task_id == task_id)

    @property
    def spent(self) -> int:
        """The measured tokens every task of this queue has spent so far."""
        return sum(e.tokens for e in self.entries)


def queue_dir(cfg: Config) -> str:
    return os.path.join(cfg.state_dir, QUEUE_SUBDIR)


def queue_path(cfg: Config, queue_id: str) -> str:
    if not _QUEUE_ID.match(queue_id):
        raise QueueError(f"{queue_id!r} is not a queue id")
    return os.path.join(queue_dir(cfg), f"{queue_id}.json")


def new_queue_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"q-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def save(cfg: Config, queue: Queue) -> str:
    """Write the queue file whole, through a rename, so a kill never leaves half of it."""
    path = queue_path(cfg, queue.queue_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".queue-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(queue), fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def load(cfg: Config, queue_id: str) -> Queue:
    path = queue_path(cfg, queue_id)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise QueueError(f"no queue {queue_id} in {queue_dir(cfg)}") from None
    entries = [Entry(**e) for e in data.pop("entries")]
    return Queue(entries=entries, **data)


def _title(description: tasks.Description) -> str:
    """The first line of the goal, cut to fit: a description has no title of its own."""
    line = next((ln.strip() for ln in description.goal.splitlines() if ln.strip()), "")
    line = line.lstrip("#").strip()
    return line if len(line) <= TITLE_WIDTH else line[: TITLE_WIDTH - 1] + "…"


def _depends_on(cfg: Config, task_id: str) -> list[str]:
    try:
        task = context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        )
    except FileNotFoundError:
        return []
    return list(task.depends_on or [])


def plan(cfg: Config, project: str, task_ids: list[str], limit: int) -> Queue:
    """The queue the confirmation screen shows, ordered, or QueueError.

    Refused before anything is shown: a repeated id, a task nobody described
    or whose description is not in sections, a limit that is not positive, and
    a cycle among the listed tasks' `depends_on`. The order is the listed one,
    moved only as far as a dependency inside the queue requires.
    """
    if limit <= 0:
        raise QueueError("the limit must be a positive number of tokens")
    if not task_ids:
        raise QueueError("a queue needs at least one task")
    if len(set(task_ids)) != len(task_ids):
        raise QueueError("a task is listed twice")
    entries: dict[str, Entry] = {}
    for task_id in task_ids:
        text = tasks.read_description(cfg.hive_tasks_dir, task_id)
        if not text:
            raise QueueError(f"task {task_id} has no description")
        try:
            description = tasks.parse_description(text)
        except tasks.DescriptionError as exc:
            raise QueueError(f"task {task_id}: the task description {exc}") from None
        entries[task_id] = Entry(task_id, _title(description), _depends_on(cfg, task_id))
    return Queue(
        queue_id=new_queue_id(),
        project=project,
        limit=limit,
        entries=_ordered(task_ids, entries),
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )


def _ordered(task_ids: list[str], entries: dict[str, Entry]) -> list[Entry]:
    """A stable topological order: each task after the queue's tasks it depends on."""
    done: list[Entry] = []
    placed: set[str] = set()
    left = list(task_ids)
    while left:
        ready = next(
            (t for t in left if all(d in placed or d not in entries for d in entries[t].depends_on)),
            None,
        )
        if ready is None:
            raise QueueError(f"the dependencies of {', '.join(left)} form a cycle")
        left.remove(ready)
        placed.add(ready)
        done.append(entries[ready])
    return done


def roles(cfg: Config) -> list[str]:
    """The roles one cycle runs, as the confirmation screen names them.

    Read from the config alone: whether the project is mapped is asked of its
    container, and the screen does not start one to find out.
    """
    shown = []
    if cfg.mapping_enabled:
        shown.append(f"{project_docs.MAPPER_ROLE} (if the project is unmapped)")
    shown += [
        "arquitecto",
        f"implementador and revisor (up to {cfg.max_revision_rounds} rounds)",
        "auditor",
    ]
    return shown


def confirmation(cfg: Config, queue: Queue) -> str:
    """What C-4 (*Amended* 2026-10-15) requires the owner to see before a yes."""
    approve = (
        "on: a task that meets every condition is merged and the base pushed"
        if cfg.auto_approves(queue.project)
        else "off: finished tasks wait for `merge-task`, and so do their dependents"
    )
    lines = [
        f"Queue {queue.queue_id} for project {queue.project}",
        "",
        "Order:",
    ]
    for n, e in enumerate(queue.entries, 1):
        after = f"  (after {', '.join(e.depends_on)})" if e.depends_on else ""
        lines.append(f"  {n}. {e.task_id}  {e.title}{after}")
    lines += [
        "",
        "Each task runs one cycle: " + ", ".join(roles(cfg)) + ".",
        "A task that fails is not retried; the tasks that depend on it are skipped.",
        f"Parallelism: {queue.parallel}",
        f"Limit: {queue.limit} measured tokens (input, output and cache, ADR 79)",
        "  It is checked before each phase, so the phase running when it is reached "
        "finishes and the spend can pass it by that one phase.",
        f"Automatic approve: {approve}",
    ]
    return "\n".join(lines)


def _usage_lines(cfg: Config, task_id: str) -> list[dict]:
    return context_transfer.read_usage(cfg.hive_tasks_dir, task_id).lines


def measured_tokens(line: dict) -> int | None:
    """One usage line's tokens as ADR 79 counts them, or None if it measured none.

    The four token fields, each per call (ADR 64), summed unweighted: any
    weighting would be an estimate of a price, and the limit is never
    estimated. `total_cost_usd` is cumulative over a session and never read.
    """
    fields = (
        line.get("input_tokens"),
        line.get("output_tokens"),
        line.get("cache_creation_input_tokens"),
        line.get("cache_read_input_tokens"),
    )
    if not line.get("measured", True) or all(f is None for f in fields):
        return None
    return sum(f for f in fields if isinstance(f, int))


def _spend(cfg: Config, entry: Entry) -> tuple[int, int]:
    """The tokens and the unmeasured calls written to the entry's log since its offset."""
    if entry.usage_offset is None:
        return 0, 0
    tokens = unmeasured = 0
    for line in _usage_lines(cfg, entry.task_id)[entry.usage_offset:]:
        counted = measured_tokens(line)
        if counted is None:
            unmeasured += 1
        else:
            tokens += counted
    return tokens, unmeasured


def _account_open(cfg: Config) -> bool:
    if core.pick_idle_account(cfg, role="arquitecto") is not None:
        return True
    return bool(core._recheck_cooling_accounts(cfg))


def wait_for_account(cfg: Config, sleep: Callable[[float], None] = time.sleep) -> None:
    """Return once an account could take a phase; free, however long it takes.

    The same look a phase takes before it gives up (`dispatch_phase`): an idle
    account, or a parked one that a fresh /usage probe released. No reset is
    booked; the queue looks again every WAIT_POLL_SECONDS.
    """
    announced = False
    while not _account_open(cfg):
        if not announced:
            logger.info("queue: no account is open; waiting for one to reset")
            announced = True
        sleep(WAIT_POLL_SECONDS)


def _comment(cfg: Config, task_id: str, body: str) -> None:
    """A comment on the task that leaves its status as it was."""
    try:
        status = context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        ).status
    except FileNotFoundError:
        status = "pending"
    tasks.handoff(cfg.hive_tasks_dir, task_id, status, body)


def _is_merged(cfg: Config, task_id: str) -> bool:
    try:
        task = context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        )
    except FileNotFoundError:
        return False
    return task.merged is not None


def _waits(cfg: Config, entry: Entry) -> bool:
    """Whether a dependency of this task is not merged yet, inside the queue or out."""
    try:
        task = context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, entry.task_id)
        )
    except FileNotFoundError:
        return False
    return bool(tasks.unmet_dependencies(cfg.hive_tasks_dir, task))


def resume(cfg: Config, queue_id: str) -> Queue:
    """Load a queue to go on with, settling what a kill left `running`.

    A task found `running` may have spent, may hold a live lock in a container
    still at work, and may be half merged: it is recorded `interrupted` with
    what its log says it spent, and neither started again nor touched on the
    board. Its dependents are skipped like any failed task's.
    """
    queue = load(cfg, queue_id)
    for entry in queue.entries:
        if entry.state == RUNNING:
            entry.tokens, entry.unmeasured = _spend(cfg, entry)
            entry.state = INTERRUPTED
            entry.note = "the queue was killed during its cycle; not started again"
            logger.warning("queue %s: task %s was interrupted", queue_id, entry.task_id)
    if queue.status != Q_PUSH_REFUSED:
        queue.status = Q_RUNNING
    save(cfg, queue)
    return queue


def run(
    cfg: Config,
    queue: Queue,
    sleep: Callable[[float], None] = time.sleep,
) -> Queue:
    """Run the queue until it is done, waits on a merge, hits its limit or a refused push.

    Saved before every cycle and after it, so the file is never behind by more
    than the one task it says is `running`.
    """
    if queue.status == Q_PUSH_REFUSED:
        logger.error("queue %s stopped on a refused push; nothing more runs", queue.queue_id)
        return queue
    queue.status = Q_RUNNING
    save(cfg, queue)
    while True:
        if queue.spent >= queue.limit:
            _stop_on_limit(cfg, queue, None)
            return queue
        entry = _next(cfg, queue)
        if entry is None:
            left = [e for e in queue.entries if e.state in (PENDING, FINISHED)]
            queue.status = Q_WAITING if any(e.state == PENDING for e in left) else Q_DONE
            save(cfg, queue)
            return queue

        wait_for_account(cfg, sleep)
        entry.usage_offset = len(_usage_lines(cfg, entry.task_id))
        entry.state = RUNNING
        save(cfg, queue)

        limit_hit = False
        refused: list[str] = []

        def may_start_phase() -> bool:
            nonlocal limit_hit
            spent = queue.spent - entry.tokens + _spend(cfg, entry)[0]
            limit_hit = spent >= queue.limit
            return not limit_hit

        def on_approve(result: core.ApproveResult) -> None:
            if result.push is not None and result.push.refused:
                refused.append(result.push.detail or "the push was refused")

        outcome = core.run_task_cycle(
            cfg, entry.task_id, queue.project,
            authorized=queue.authorized,
            on_approve=on_approve,
            may_start_phase=may_start_phase,
        )
        entry.tokens, entry.unmeasured = _spend(cfg, entry)
        _settle(cfg, queue, entry, outcome, limit_hit)
        if entry.state == PENDING:
            # Held before any phase spent: the account it was given closed
            # again. Without a pause the account check can pass at once and
            # the same hold repeat in a tight loop.
            save(cfg, queue)
            sleep(WAIT_POLL_SECONDS)
            continue
        if refused:
            queue.status = Q_PUSH_REFUSED
            entry.note = f"merged, but the push of the base was refused: {refused[0]}"
            save(cfg, queue)
            logger.error("queue %s stops: %s", queue.queue_id, entry.note)
            return queue
        if entry.state == STOPPED:
            _stop_on_limit(cfg, queue, entry)
            return queue
        save(cfg, queue)


def _next(cfg: Config, queue: Queue) -> Entry | None:
    """The first pending task that can start, skipping what depends on a failure."""
    for entry in queue.entries:
        if entry.state != PENDING:
            continue
        failed = [
            d for d in entry.depends_on
            if any(e.task_id == d and e.state in FAILED for e in queue.entries)
        ]
        if failed:
            entry.state = SKIPPED
            entry.note = f"skipped: {', '.join(failed)} did not finish in this queue"
            _comment(
                cfg, entry.task_id,
                f"## queue {queue.queue_id}\n\nNot started: it depends on "
                f"{', '.join(failed)}, which did not finish in this queue. "
                "Run it on its own once that is settled.",
            )
            save(cfg, queue)
            logger.warning("queue %s: task %s %s", queue.queue_id, entry.task_id, entry.note)
            continue
        if _waits(cfg, entry):
            continue
        return entry
    return None


def _settle(
    cfg: Config, queue: Queue, entry: Entry, outcome: core.CycleOutcome, limit_hit: bool
) -> None:
    """The state one cycle's outcome leaves its task in."""
    if outcome is core.CycleOutcome.FINISHED:
        entry.state = MERGED if _is_merged(cfg, entry.task_id) else FINISHED
    elif outcome is core.CycleOutcome.HELD and limit_hit:
        entry.state = STOPPED
    elif outcome is core.CycleOutcome.HELD and entry.tokens == 0 and entry.unmeasured == 0:
        # No phase of it ran: an account was open when the queue looked and
        # gone when the phase asked. Nothing was spent, so the cycle never
        # began; the task goes back to wait for quota with the rest.
        entry.state = PENDING
        entry.usage_offset = None
    elif outcome is core.CycleOutcome.HELD:
        entry.state = HELD
    elif outcome is core.CycleOutcome.SPLIT:
        entry.state = SPLIT
    else:
        entry.state = BLOCKED
    logger.info("queue %s: task %s is %s", queue.queue_id, entry.task_id, entry.state)


def _stop_on_limit(cfg: Config, queue: Queue, entry: Entry | None) -> None:
    queue.status = Q_LIMIT
    if entry is not None:
        entry.note = "stopped by the queue's limit before its next phase"
    save(cfg, queue)
    logger.warning(
        "queue %s reached its limit: %d of %d measured tokens",
        queue.queue_id, queue.spent, queue.limit,
    )


def summary(queue: Queue) -> str:
    """The queue's end, printed: until notifications exist, this is how the owner is told."""
    lines = [f"Queue {queue.queue_id}: {queue.status}, {queue.spent} of {queue.limit} tokens"]
    for e in queue.entries:
        extra = f"  {e.tokens} tokens" if e.usage_offset is not None or e.tokens else ""
        if e.unmeasured:
            extra += f", {e.unmeasured} unmeasured call(s)"
        note = f"  {e.note}" if e.note else ""
        lines.append(f"  {e.task_id}: {e.state}{extra}{note}")
    return "\n".join(lines)
