# dispatcher/adr_numbers.py
"""ADR numbers, handed out per project so two arquitectos never write the same one.

`docs/plans/autonomy.md` *Decisions*, 7, and ADR 81. Two tasks planned at once
would each read `docs/decisions.md`, see the same highest number and append the
same next one; the union merge then keeps both entries and the log has two
ADR 12s. So the dispatcher hands the numbers out instead: a counter per project
in `state_dir`, seeded from the highest number the project's log holds, and a
block of numbers per task, taken when its arquitecto starts.

- A task keeps its block. A re-run of the same task's arquitecto (a resume, a
  `run-phase`, a cycle run again after a block) is given the block it already
  has, never a second one.
- The counter only moves forward. It is re-seeded on every reservation, so an
  ADR someone appended by hand past the counter is stepped over, not reused.
- An unused number is a gap. Gaps are allowed: the log is ordered by number,
  not dense, and the duplicate gate (`gates.decisions_log`) is what catches a
  number used twice.

The state file is written the way the account state is (`state_machine`): a
temporary file in the same directory and `os.replace`, under an `flock` held
only around the read-modify-write, so a crash leaves the old file or the new
one and two dispatcher processes never hand out the same block.
"""
from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

#: How many numbers a task is given. A guess (`autonomy.md`, *Open
#: questions*): most tasks write one ADR or none.
DEFAULT_BLOCK = 3

#: The subdirectory of `state_dir` the counters live in, one file per project,
#: apart from the account state files at the top level.
STATE_SUBDIR = "adr"

#: An ADR heading: `## ADR 12 — ...`, or `## ~~ADR 12 — ...~~` once struck
#: through, which still holds its number.
_ADR_HEADING = re.compile(r"^##\s+(?:~~)?\s*ADR\s+(\d+)\b")

_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclasses.dataclass(frozen=True)
class Block:
    """The ADR numbers one task may use, `first` to `last` inclusive."""

    first: int
    last: int

    def prompt_line(self) -> str:
        """The line the arquitecto's prompt carries."""
        return (
            f"Your ADR numbers are {self.first} to {self.last}: number any ADR this task "
            "appends to docs/decisions.md from that block, in order, instead of taking the "
            "next number the file shows. Other tasks are planned at the same time and hold "
            "the numbers around yours. An unused number is left as a gap, which is allowed."
        )


def headings(text: str) -> list[str]:
    """The level-two headings of a markdown text, outside fenced code."""
    found: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and line.startswith("## "):
            found.append(line.rstrip())
    return found


def adr_numbers(text: str) -> list[int]:
    """Every ADR number a decisions log's headings use, in file order."""
    numbers: list[int] = []
    for heading in headings(text):
        match = _ADR_HEADING.match(heading)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def highest(text: str | None) -> int:
    """The highest ADR number in a decisions log; 0 for none or no log."""
    return max(adr_numbers(text or ""), default=0)


def _state_path(state_dir: str, slug: str) -> str:
    return os.path.join(state_dir, STATE_SUBDIR, f"{slug}.json")


def _read(path: str) -> dict:
    if not os.path.exists(path):
        return {"next": 1, "blocks": {}}
    data = json.loads(Path(path).read_text())
    data.setdefault("next", 1)
    data.setdefault("blocks", {})
    return data


def _write(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False)
    try:
        tmp.write(json.dumps(data, indent=2, sort_keys=True))
    finally:
        tmp.close()
    os.replace(tmp.name, path)


@contextlib.contextmanager
def _locked(state_dir: str, slug: str):
    """The project's counter lock; a new descriptor per acquisition, as in
    `state_machine._locked`, so two threads exclude each other too."""
    directory = os.path.join(state_dir, STATE_SUBDIR)
    Path(directory).mkdir(parents=True, exist_ok=True)
    fd = os.open(os.path.join(directory, f"{slug}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def reserve(
    state_dir: str,
    slug: str,
    task_id: str,
    decisions_text: str | None,
    size: int = DEFAULT_BLOCK,
) -> Block:
    """The task's block of ADR numbers, taken now or the one it already holds.

    `decisions_text` is the project's `docs/decisions.md` as the base has it,
    or None when there is none; it seeds the counter, which then never goes
    below the highest number in it plus one.
    """
    if size < 1:
        raise ValueError(f"an ADR block holds at least one number, not {size}")
    path = _state_path(state_dir, slug)
    with _locked(state_dir, slug):
        data = _read(path)
        held = data["blocks"].get(task_id)
        if held is not None:
            return Block(int(held[0]), int(held[1]))
        first = max(int(data["next"]), highest(decisions_text) + 1)
        block = Block(first, first + size - 1)
        data["blocks"][task_id] = [block.first, block.last]
        data["next"] = block.last + 1
        _write(path, data)
    return block

