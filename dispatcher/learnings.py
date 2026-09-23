# dispatcher/learnings.py
"""Traps, written down where a dead phase cannot take them with it.

A phase that steps on a trap — a fixture that has to exist before the suite
runs, a build that needs an env var nobody wrote down — learns something every
later task would otherwise pay for again. The handoff already carries proposed
learnings, but a handoff only reaches the next phase of the *same* task, and
only if the phase lived long enough to return one: a run that hits its turn
budget or its timeout loses everything it had not put in a file.

So an entry is a file, written by the phase itself, under a directory that is
outside every worktree and mounted into every container. It outlives the
phase, the task branch and the worktree, and any task in any project can grep
it without waiting for a merge.

The dispatcher's half of this runs with no model in the loop: it makes the
directories, carries a task's entries forward, drops them once the branch that
filed them merged, and unpicks them when the task they were riding died.
Everything that needs judgement — is this a real trap, does it belong to every
project — is the auditor's job, or a human's.

Two scopes, because they have different blast radii. `scope: project` is a
trap in one codebase and the auditor files it into that project's
`docs/learnings/`. `scope: harness` is a trap in what every project shares —
the container, the CLI, the worktree — and it reaches other projects only
after a human has moved it into the cross-project store, which is the only
thing in here no automated path writes to.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

#: Beside the task files rather than in any repo: the dispatcher container
#: does not mount this source tree, and an entry that only exists on a task
#: branch is invisible to the task that needed it. `.hive/` is gitignored and
#: bind-mounted at the same path on all three containers, so a path built here
#: can be quoted verbatim in a prompt and the agent will find it.
ROOT_NAME = "learnings"
INBOX_NAME = "inbox"
HARNESS_NAME = "harness"

UNCONFIRMED = "unconfirmed"
CONFIRMED = "confirmed"

SCOPE_PROJECT = "project"
SCOPE_HARNESS = "harness"

#: Rows of the table a phase is handed. A prompt that grows with the inbox
#: would quietly tax every phase of every task, so the table is capped and the
#: rest is left to the grep the fragment asks for. When this starts truncating
#: often, the fix is the filtering the README describes — rows matching the
#: task's profile, labels or the paths its plan names — not a bigger cap.
MAX_ROWS = 40

#: One row has to stay a row. Longer than this and the entry file is the place
#: for it, which is what the `#` column points at.
_CELL_CHARS = 160


def root_dir(hive_dir: str) -> str:
    """The learnings root, derived from where the task files live.

    Derived rather than configured because it has to be identical on both
    sides of the mount: `hive_tasks_dir: /data/.hive/tasks` makes this
    `/data/.hive/learnings`, on the dispatcher and in both agent containers.
    A second config key is a second thing that can disagree.
    """
    return os.path.join(os.path.dirname(os.path.normpath(hive_dir)), ROOT_NAME)


def inbox_dir(hive_dir: str) -> str:
    return os.path.join(root_dir(hive_dir), INBOX_NAME)


def harness_dir(hive_dir: str) -> str:
    return os.path.join(root_dir(hive_dir), HARNESS_NAME)


def ensure_dirs(hive_dir: str) -> str:
    """Make both directories, the way the scratch directory is made.

    A phase told to write a file somewhere should find the somewhere already
    there: `mkdir -p` costs a turn, and a phase that is about to die is
    exactly the one that will not spend it.
    """
    root = root_dir(hive_dir)
    for path in (inbox_dir(hive_dir), harness_dir(hive_dir)):
        os.makedirs(path, exist_ok=True)
    return root


_FRONTMATTER_DELIM = "---"
_HEADING_RE = re.compile(r"^#{1,6}[ \t]*(?P<title>.+?)[ \t]*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```[^\n]*\n(?P<code>.*?)```", re.DOTALL)
#: Two runs of the same failure differ in pids, timings, temp paths and hex
#: addresses. Fingerprinting is what decides "another task hit the same wall",
#: so all of that is flattened before comparing.
_VOLATILE_RE = re.compile(r"[0-9a-f]{6,}|\d+")
_NOISE_RE = re.compile(r"[^a-z0-9]+")


def _split(text: str) -> tuple[dict | None, str]:
    if not text.lstrip().startswith(_FRONTMATTER_DELIM):
        return None, text
    try:
        _, fm_text, body = text.split(_FRONTMATTER_DELIM, 2)
        loaded = yaml.safe_load(fm_text)
    except (ValueError, yaml.YAMLError):
        return None, text
    if not isinstance(loaded, dict):
        return None, text
    return loaded, body.lstrip("\n")


def _section(body: str, title: str) -> str:
    """The text under one `## Heading`, up to the next heading of any level."""
    for match in _HEADING_RE.finditer(body):
        if match.group("title").strip().lower() != title.lower():
            continue
        rest = body[match.end():]
        following = _HEADING_RE.search(rest)
        return (rest[: following.start()] if following else rest).strip()
    return ""


@dataclasses.dataclass
class Entry:
    """One trap, as it sits on disk.

    The frontmatter is kept as the dict it was read as, not unpacked into
    fields: the dispatcher only writes four of its keys, and an entry written
    by a phase that added one of its own should survive a rewrite with that
    key intact.
    """

    path: str
    #: What a prompt cites and a human passes to the CLI: the path relative to
    #: the learnings root, so `inbox/x.md` and `harness/x.md` are one pointer
    #: that says which side of the human review the entry is on.
    ref: str
    meta: dict
    body: str

    def _str(self, key: str) -> str:
        return str(self.meta.get(key) or "").strip()

    @property
    def project(self) -> str:
        return self._str("project")

    @property
    def task(self) -> str:
        return self._str("task")

    @property
    def scope(self) -> str:
        return self._str("scope") or SCOPE_PROJECT

    @property
    def status(self) -> str:
        return self._str("status") or UNCONFIRMED

    @property
    def when(self) -> str:
        return self._str("when")

    @property
    def carried_by(self) -> str:
        return self._str("carried_by")

    @property
    def reviewed(self) -> bool:
        """Whether a human has already moved this into the shared store."""
        return self.ref.startswith(f"{HARNESS_NAME}{os.sep}")

    @property
    def rule(self) -> str:
        """The one line the table shows: what to do instead."""
        rule = _section(self.body, "Rule").splitlines()
        for line in rule:
            if line.strip():
                return line.strip().lstrip("- ")
        return self.when

    @property
    def fingerprint(self) -> str:
        """What "the same wall" means, for the confirmation rule.

        The verbatim error is the part two tasks share; everything else about
        an entry is how its author chose to word it. An entry with no symptom
        falls back to its trigger line, and one with neither gets an empty
        fingerprint and simply never corroborates anything.
        """
        symptom = _section(self.body, "Symptom")
        fenced = _FENCE_RE.search(symptom)
        raw = (fenced.group("code") if fenced else symptom) or self.when
        flattened = _VOLATILE_RE.sub("0", raw.lower())
        return _NOISE_RE.sub(" ", flattened).strip()[:200]


def _read_entry(root: str, path: str) -> Entry | None:
    try:
        text = Path(path).read_text()
    except OSError as exc:
        logger.warning("learnings: could not read %s: %s", path, exc)
        return None
    meta, body = _split(text)
    if meta is None:
        # Left where it is rather than deleted or repaired: a file the parser
        # does not understand is still something a phase wrote on purpose, and
        # the grep the agents are told to run finds it anyway.
        logger.warning("learnings: %s has no usable frontmatter; not tracking it", path)
        return None
    return Entry(path=path, ref=os.path.relpath(path, root), meta=meta, body=body)


def read_dir(root: str, path: str) -> list[Entry]:
    if not os.path.isdir(path):
        return []
    entries = []
    for name in sorted(os.listdir(path)):
        if not name.endswith(".md"):
            continue
        entry = _read_entry(root, os.path.join(path, name))
        if entry is not None:
            entries.append(entry)
    return entries


def read_inbox(hive_dir: str) -> list[Entry]:
    return read_dir(root_dir(hive_dir), inbox_dir(hive_dir))


def read_harness(hive_dir: str) -> list[Entry]:
    return read_dir(root_dir(hive_dir), harness_dir(hive_dir))


def read_all(hive_dir: str) -> list[Entry]:
    return read_inbox(hive_dir) + read_harness(hive_dir)


def _write(entry: Entry, path: str | None = None) -> None:
    """Rewrite an entry in place, atomically.

    Same reason the task file is written this way: an agent greps this
    directory whenever it is debugging, and half a file is worse than a stale
    one. The mode is set explicitly because the umask differs between the
    dispatcher and the container that wrote the file first.
    """
    target = path or entry.path
    text = (
        f"{_FRONTMATTER_DELIM}\n"
        f"{yaml.safe_dump(entry.meta, sort_keys=False, allow_unicode=True)}"
        f"{_FRONTMATTER_DELIM}\n\n{entry.body.strip()}\n"
    )
    directory = os.path.dirname(target)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".learning-", suffix=".md")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _tasks_by_fingerprint(entries: list[Entry]) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    for entry in entries:
        fingerprint = entry.fingerprint
        if not fingerprint:
            continue
        # Keyed by task, not by file: an entry promoted into the shared store
        # keeps its original task id, so it cannot corroborate itself.
        seen.setdefault(fingerprint, set()).add(entry.task or entry.ref)
    return seen


def reconcile(hive_dir: str) -> list[str]:
    """Confirm the entries a second task has now hit.

    The poisoning guard: one phase can write anything, so an entry is a claim
    until something independent backs it. Two distinct tasks reporting the
    same error is that something, and it is checkable without a model — which
    is the point, since the alternative is paying a phase to review claims.
    """
    entries = read_all(hive_dir)
    seen = _tasks_by_fingerprint(entries)
    confirmed = []
    for entry in entries:
        if entry.reviewed or entry.status == CONFIRMED:
            continue
        if len(seen.get(entry.fingerprint, ())) < 2:
            continue
        entry.meta["status"] = CONFIRMED
        _write(entry)
        confirmed.append(entry.ref)
    if confirmed:
        logger.info("learnings: confirmed by a second task: %s", ", ".join(confirmed))
    return confirmed


def _carried(entries: list[Entry], project: str) -> list[Entry]:
    """This project's entries that are still waiting to be filed.

    Harness-scoped ones are left out on purpose: the auditor files into the
    project's docs, and a trap in the shared environment does not belong in
    one project's `docs/learnings/`. Those wait in the inbox for a human.
    """
    return [
        entry
        for entry in entries
        if not entry.reviewed and entry.scope == SCOPE_PROJECT and entry.project == project
    ]


def carry(hive_dir: str, project: str, task_id: str) -> list[str]:
    """Hand this project's open entries to the task about to file them.

    Called when the auditor is dispatched. The stamp is overwritten rather
    than respected: an entry whose previous carrier died is exactly the one
    the next task has to pick up, and the dead carrier is no longer around to
    release it.
    """
    carried = []
    for entry in _carried(read_inbox(hive_dir), project):
        if entry.carried_by == task_id:
            continue
        entry.meta["carried_by"] = task_id
        _write(entry)
        carried.append(entry.ref)
    if carried:
        logger.info("task %s carries inbox entries: %s", task_id, ", ".join(carried))
    return carried


def drop_promoted(hive_dir: str, task_id: str) -> list[str]:
    """Delete the entries whose branch just merged.

    They are in the project's `docs/learnings/` now, committed, so keeping
    them here would charge every later phase of every task for a row it can
    already read in the repo. The risk is the other way: an auditor that ran
    out of turns before filing one loses it here. That is why every deletion
    is logged by ref — the inbox is not the only copy, the merge commit is.
    """
    dropped = []
    for entry in read_inbox(hive_dir):
        if entry.reviewed or entry.scope != SCOPE_PROJECT:
            continue
        if task_id not in (entry.task, entry.carried_by):
            continue
        Path(entry.path).unlink(missing_ok=True)
        dropped.append(entry.ref)
    if dropped:
        logger.info("task %s merged: dropped inbox entries %s", task_id, ", ".join(dropped))
    return dropped


def mark_orphaned(hive_dir: str, task_id: str) -> list[str]:
    """Unpick a dead task's entries and leave them for the next one.

    A task that ended blocked, failed, or had its branch discarded filed
    nothing, so its entries have to stay — but they cannot keep claiming a
    carrier that is gone, and a status this task alone vouched for is back to
    being one phase's word. An entry a *different* task also reported keeps
    its confirmation: that sighting did not die with this task.
    """
    entries = read_inbox(hive_dir)
    seen = _tasks_by_fingerprint(entries + read_harness(hive_dir))
    orphaned = []
    for entry in entries:
        if entry.reviewed:
            continue
        if task_id not in (entry.task, entry.carried_by):
            continue
        touched = False
        if entry.carried_by == task_id:
            entry.meta.pop("carried_by", None)
            previous = entry.meta.get("orphaned_from")
            history = [str(item) for item in previous] if isinstance(previous, list) else []
            if task_id not in history:
                # Capped: this is a breadcrumb for whoever wonders why an
                # entry has been in here for a month, not an audit log.
                entry.meta["orphaned_from"] = (history + [task_id])[-5:]
            touched = True
        corroborated = len(seen.get(entry.fingerprint, ()) - {task_id}) >= 1
        if entry.status == CONFIRMED and entry.task == task_id and not corroborated:
            entry.meta["status"] = UNCONFIRMED
            touched = True
        if touched:
            _write(entry)
            orphaned.append(entry.ref)
    if orphaned:
        logger.info("task %s did not finish: released inbox entries %s", task_id, ", ".join(orphaned))
    return orphaned


def resolve(hive_dir: str, ref: str) -> Entry | None:
    """One entry, by anything a human would type at it."""
    root = root_dir(hive_dir)
    candidates = [ref, f"{ref}.md", os.path.join(INBOX_NAME, ref), os.path.join(INBOX_NAME, f"{ref}.md"),
                  os.path.join(HARNESS_NAME, ref), os.path.join(HARNESS_NAME, f"{ref}.md")]
    for candidate in candidates:
        path = os.path.join(root, candidate)
        if os.path.isfile(path):
            return _read_entry(root, path)
    return None


def promote(hive_dir: str, ref: str) -> Entry | None:
    """Move an entry into the cross-project store. Human-run, by design.

    This is the only way anything reaches `harness/`, and no role and no
    dispatcher path calls it: a trap in the shared environment is a claim
    about every project at once, on the word of one phase of one task, and
    that is the one place where a wrong entry costs the most to undo.
    """
    entry = resolve(hive_dir, ref)
    if entry is None or entry.reviewed:
        return None
    entry.meta["scope"] = SCOPE_HARNESS
    entry.meta["status"] = CONFIRMED
    entry.meta.pop("carried_by", None)
    target = os.path.join(harness_dir(hive_dir), os.path.basename(entry.path))
    _write(entry, path=target)
    Path(entry.path).unlink(missing_ok=True)
    return _read_entry(root_dir(hive_dir), target)


def set_status(hive_dir: str, ref: str, status: str) -> Entry | None:
    """A human's yes or no on one entry, without waiting for a second task."""
    entry = resolve(hive_dir, ref)
    if entry is None:
        return None
    entry.meta["status"] = status
    _write(entry)
    return entry


def delete(hive_dir: str, ref: str) -> str | None:
    entry = resolve(hive_dir, ref)
    if entry is None:
        return None
    Path(entry.path).unlink(missing_ok=True)
    return entry.ref


def applicable(entries: list[Entry], project: str) -> list[Entry]:
    """The entries one project's phases are shown.

    Everything a human has already reviewed, plus everything this project
    discovered itself — including its own harness-scoped entries, which are
    unreviewed but were found right here. Another project's unreviewed
    entries are the ones held back, because that is the whole difference the
    review makes.
    """
    return [entry for entry in entries if entry.reviewed or entry.project == project]


def _cell(text: str) -> str:
    flat = " ".join((text or "").split()).replace("|", "\\|")
    return flat if len(flat) <= _CELL_CHARS else f"{flat[:_CELL_CHARS - 1]}…"


def table(entries: list[Entry]) -> str:
    """The rows, as the index everywhere else in the harness renders them."""
    # Confirmed first, so that a table cut off at MAX_ROWS keeps the entries
    # something independent has already backed; ref second, so the order is
    # stable between two phases of the same task.
    ordered = sorted(entries, key=lambda e: (e.status != CONFIRMED, e.ref))
    rows = [
        f"| `{entry.ref}` | {_cell(entry.rule)} | {_cell(entry.when)} | {entry.status} |"
        for entry in ordered[:MAX_ROWS]
    ]
    lines = ["| # | Learning | When it applies | Status |", "|---|---|---|---|", *rows]
    if len(ordered) > MAX_ROWS:
        lines.append("")
        lines.append(
            f"{len(ordered) - MAX_ROWS} more are in the directory but not in this table; "
            "the grep finds them."
        )
    return "\n".join(lines)


_READ = (
    "Traps earlier tasks stepped on are in `{root}/`, one file per entry, outside every "
    "worktree and shared by every task this harness runs. Before you spend a turn debugging "
    "anything, grep that directory for the exact error text in front of you: entries quote "
    "their errors verbatim precisely so that a grep for the message finds them. An entry "
    "marked unconfirmed was reported once and never reproduced — read it, do not trust it."
)

_WRITE = (
    "Going the other way: when something costs you time that the next task would lose too, "
    "write it down the moment you understand it, not when you finish. A phase that runs out of "
    "turns or times out takes with it everything it had not written to a file, and this "
    "directory is the one place a note outlives the phase. Add a new file — never edit an entry "
    "that is already there, because other tasks are writing here at the same time and one file "
    "per entry is what keeps them from colliding:\n\n"
    "```\n"
    "{inbox}/{task_id}-<short-slug>.md\n"
    "---\n"
    "project: {project}\n"
    "task: {task_id}\n"
    "phase: {role}\n"
    "scope: project        # or harness\n"
    "status: unconfirmed\n"
    "when: one line saying when this applies, as a condition the next agent can check\n"
    "---\n"
    "## Symptom\n"
    "The error, verbatim, in a fenced block.\n"
    "## Why\n"
    "What was actually wrong.\n"
    "## Rule\n"
    "What to do instead. One line: this is the row every later phase reads.\n"
    "## Evidence\n"
    "The command that produced the symptom, and where you ran it.\n"
    "```\n\n"
    "`scope: project` is a trap in this project's own code, config or tests. `scope: harness` is "
    "a trap in what every project shares — the container, the CLI, the worktree, the tooling — "
    "and it reaches other projects only once a human has read it. Write `status: unconfirmed` "
    "and leave it alone: an entry is confirmed when a second task hits the same wall or a human "
    "says so, and neither of those is your call. A trap is \"do not step on this\"; work you "
    "chose not to finish is debt, and that goes in your handoff instead."
)

_AUDITOR = (
    "The entries this task carries are the rows above whose file says `carried_by: {task_id}`, "
    "plus any this task wrote. File them into the project's learnings on this branch alongside "
    "the proposals from the handoffs, keeping each error text verbatim and each unconfirmed "
    "entry marked unconfirmed. Leave the files themselves alone: they are dropped when this "
    "branch merges, and deleting one by hand loses it for every task that has not seen it yet."
)

#: Every role that runs against the code. The mapper is in it too: "the build
#: command in the README does not work" is exactly what mapping a project
#: turns up, and it is a trap for everything that comes after.
_ROLES = ("cartografo", "arquitecto", "implementador", "revisor", "auditor")


def duties(role: str, task_id: str, project: str, hive_dir: str) -> str:
    """The inbox, as a prompt fragment for one role.

    Empty for a role that does not run against the code, the same way an
    unknown role gets no skills: a role added elsewhere degrades to saying
    nothing rather than to an error in the middle of a dispatch.
    """
    if role not in _ROLES:
        return ""
    root = root_dir(hive_dir)
    parts = [
        _READ.format(root=root),
        _WRITE.format(inbox=inbox_dir(hive_dir), task_id=task_id, project=project, role=role),
    ]
    rows = applicable(read_all(hive_dir), project)
    if rows:
        parts.append(f"Entries that apply here, by file under `{root}/`:\n\n{table(rows)}")
        if role == "auditor":
            parts.append(_AUDITOR.format(task_id=task_id))
    return "\n\n".join(parts)
