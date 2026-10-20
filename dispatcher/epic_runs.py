"""A started epic's run, read against the hive (ADR 218).

`actions` keeps the run's file under the queue's lock; this module is what
the run needs from the hive: the epic's tasks now in Ready, each task's usage
log offset, and the run's spend. The spend is ADR 79's measure, the tokens a
phase's `result` wrote to its task's usage log (`measured_tokens`), counted
from the offset each task had when the run queued it. Nothing is estimated:
a line that measured nothing is counted apart, as `unmeasured`.

Both the board service (*Start epic*) and the dispatcher (the follow-on of a
merge, `follow_on`) call it; neither imports the other.
"""

from __future__ import annotations

from dispatcher import actions, context_transfer, notifications, tasks


def usage_offset(hive_dir: str, task_id: str) -> int:
    """The number of records in the task's usage log now."""
    return len(context_transfer.read_usage(hive_dir, task_id).lines)


def task_spend(hive_dir: str, task_id: str, start: int) -> tuple[int, int]:
    """One task's measured tokens and unmeasured calls since its usage offset `start`."""
    tokens = unmeasured = 0
    for line in context_transfer.read_usage(hive_dir, task_id).lines[start:]:
        counted = context_transfer.measured_tokens(line)
        if counted is None:
            unmeasured += 1
        else:
            tokens += counted
    return tokens, unmeasured


def spend(hive_dir: str, run: dict) -> tuple[int, int]:
    """The run's measured tokens and its unmeasured calls since each task's offset."""
    tokens = unmeasured = 0
    for task_id, entry in sorted(run.get("queued", {}).items()):
        start = entry.get("usage_offset") if isinstance(entry, dict) else None
        if type(start) is not int:
            continue
        counted, missing = task_spend(hive_dir, task_id, start)
        tokens += counted
        unmeasured += missing
    return tokens, unmeasured


def ready_tasks(hive_dir: str, epic_id: str) -> list[str]:
    """The epic's tasks (its `split_into`) now in Ready, in the board's order."""
    board = tasks.read_all_tasks(hive_dir)
    epic = board.get(epic_id)
    if epic is None or epic.status != "epic":
        return []
    covered = epic.split_into if isinstance(epic.split_into, list) else []
    return [tid for tid in tasks.ready_order(board) if tid in covered]


def view(hive_dir: str, run: dict) -> dict:
    """The run as the console reads it: the file, its spend, and the limit's state.

    `limit_reached` is true when the run is paused at its limit, or when its
    spend has reached the limit and no merge has looked since (the pause is
    written at the next queue write, `follow_on` or *Start epic*).
    """
    tokens, unmeasured = spend(hive_dir, run)
    limit = run.get("limit")
    return {
        **{k: run.get(k) for k in actions.RUN_FIELDS},
        "spent": {"tokens": tokens, "unmeasured": unmeasured},
        "limit_reached": run.get("paused") == actions.PAUSED_LIMIT
        or (type(limit) is int and tokens >= limit),
    }


def follow_on(hive_dir: str, state_dir: str) -> list[str]:
    """After a merge, each started epic queues its tasks now Ready (ADR 218).

    Every run still going is looked at, not only the merged task's epic: a
    task of one epic may wait on a task of another. One line per epic that
    queued or paused; a run that cannot be read is skipped, and an epic's
    failure is a line rather than a raise, so the merge record stands.
    """
    runs, _ = actions.read_epic_runs(state_dir)
    lines = []
    for run in runs:
        epic_id = run["epic"]
        if run.get("stopped_at") is not None or run.get("paused"):
            continue
        try:
            made = actions.follow_epic_run(
                state_dir, epic_id, lambda: ready_tasks(hive_dir, epic_id),
                offset=lambda tid: usage_offset(hive_dir, tid),
                spent=lambda r: spend(hive_dir, r)[0],
            )
        except (OSError, ValueError, actions.ActionRefused) as exc:
            lines.append(f"epic {epic_id}: follow-on failed: {exc}")
            continue
        if made["paused"]:
            lines.append(f"epic {epic_id}: paused, its limit is reached")
            # Paused waits on the person raising the limit or stopping the
            # run: the *Blocked* kind (ADR 221). `paused` is true on the
            # move into it only, so a paused run is told once.
            notifications.notify_task(
                hive_dir, "blocked", epic_id, "paused: its spending limit is reached",
                _epic_title(hive_dir, epic_id),
            )
        elif made["queued"]:
            ids = ", ".join(r["task_id"] for r in made["queued"])
            lines.append(f"epic {epic_id}: queued {ids}")
    return lines


def _epic_title(hive_dir: str, epic_id: str) -> str | None:
    """The epic's frontmatter title for a notification, or None when unreadable."""
    try:
        return context_transfer.read_task_file(
            context_transfer.task_file_path(hive_dir, epic_id)
        ).title
    except Exception:  # a bad file is the board's problem, not this title's
        return None
