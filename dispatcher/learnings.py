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
filed them merged, unpicks them when the task they were riding died, and
retires the ones the harness has outgrown. Everything that needs judgement — is
this a real trap, does it belong to every project — is the auditor's job, or a
human's.

Two scopes, because they have different blast radii. `scope: project` is a
trap in one codebase and the auditor files it into that project's
`docs/learnings/`. `scope: harness` is a trap in what every project shares —
the container, the CLI, the worktree — and it reaches other projects only
after a human has moved it into the cross-project store, which is the only
thing in here no automated path writes to.

Nothing in here is true for ever, and an entry is a claim about a harness as
much as about a project: "`node --test` is refused" was right until
`allowed_tools` was wired, and "a review phase's edits never reach the branch"
was right until the auditor was given the writers' worktree. Both outlived
their truth in the inbox, and three phases of one task spent turns arguing with
the first of them. So there are two ways out for an entry besides deletion.
Every entry is stamped with a fingerprint of the permission surface it was
written under, and one written under a different surface is *stale*: still
shown, because it may well still be right, but no longer counted as evidence.
And a task that walked through another task's trap unharmed can say so, by
writing `refutes: <ref>` on an entry of its own — which retires the old one
without destroying either the claim or the correction.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path

import yaml

from dispatcher import context_transfer

logger = logging.getLogger(__name__)

#: Beside the task files rather than in any repo: the dispatcher container
#: does not mount this source tree, and an entry that only exists on a task
#: branch is invisible to the task that needed it. `.hive/` is gitignored and
#: bind-mounted at the same path on all three containers, so a path built here
#: can be quoted verbatim in a prompt and the agent will find it.
ROOT_NAME = "learnings"
INBOX_NAME = "inbox"
HARNESS_NAME = "harness"
#: Where a filed entry goes instead of being unlinked. Deliberately not
#: enumerated by `read_inbox`, `read_harness` or `read_all`: the reason the
#: inbox copy goes away is prompt cost, and a directory no reader walks costs
#: a phase nothing, so the file can be kept for the one case where the drop
#: was wrong (`docs/debt/T-012-D1.md`).
DROPPED_NAME = "dropped"

UNCONFIRMED = "unconfirmed"
CONFIRMED = "confirmed"
#: Retired on a later task's evidence, not on a guess: the trap was looked for
#: where it was reported and did not bite. The entry stays on disk and stays in
#: the human's listing; it stops reaching the phases.
REFUTED = "refuted"

SCOPE_PROJECT = "project"
SCOPE_HARNESS = "harness"

#: The role whose phase files a project-scope entry into the project's
#: `docs/learnings/`. Named once because the gate in `drop_promoted` and the
#: line `merge-task` prints when that gate fires both read it, and a role named
#: twice is two places to disagree.
PROMOTING_ROLE = "auditor"

#: Rows of the table a phase is handed. A prompt that grows with the inbox
#: would quietly tax every phase of every task, so the table is capped and the
#: rest is left to the grep the fragment asks for. When this starts truncating
#: often, the fix is the filtering the README describes — rows matching the
#: task's profile, labels or the paths its plan names — not a bigger cap.
MAX_ROWS = 40

#: Confirmed rows survive the cut first because something independent backs
#: them, refuted ones last because a later task went looking and found nothing.
_STATUS_ORDER = {CONFIRMED: 0, UNCONFIRMED: 1, REFUTED: 2}

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
    return os.path.join(context_transfer.hive_root(hive_dir), ROOT_NAME)


def inbox_dir(hive_dir: str) -> str:
    return os.path.join(root_dir(hive_dir), INBOX_NAME)


def harness_dir(hive_dir: str) -> str:
    return os.path.join(root_dir(hive_dir), HARNESS_NAME)


def dropped_dir(hive_dir: str) -> str:
    return os.path.join(root_dir(hive_dir), DROPPED_NAME)


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


def harness_fingerprint(
    permission_mode: str | None,
    allowed_tools: list[str] | None,
    writer_roles: frozenset[str] | set[str] | None,
) -> str:
    """What a phase was allowed to do, as one short string.

    Only the permission surface goes in, not the whole config: these three
    settings are what decided both entries that went false in this harness —
    whether a command is refused, and whether a phase's edits reach the branch
    — and a coarser fingerprint would mark the whole inbox stale every time a
    timeout was tuned. Primitives rather than a Config, so this module keeps
    importing nothing from the package but the path helpers.
    """
    surface = "\n".join(
        (
            f"permission_mode={permission_mode or ''}",
            "allowed_tools=" + ",".join(sorted(allowed_tools or ())),
            "writer_roles=" + ",".join(sorted(writer_roles or ())),
        )
    )
    # Short: it is written into frontmatter a human reads and compared for
    # equality, never for cryptographic anything.
    return hashlib.sha256(surface.encode()).hexdigest()[:12]


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
    def harness(self) -> str:
        """The permission surface this was written under, if it was stamped."""
        return self._str("harness")

    @property
    def refutes(self) -> str:
        """The entry this one was written to correct, as its author typed it."""
        return self._str("refutes")

    @property
    def refuted_by(self) -> str:
        return self._str("refuted_by")

    def stale(self, harness: str) -> bool:
        """Whether this was written under a harness that no longer exists.

        Both fingerprints have to be there: an entry from before the stamp
        existed is not stale, it is unknown, and treating unknown as stale
        would retire the whole inbox the first time this ran.
        """
        return bool(harness and self.harness and self.harness != harness)

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


def _match(entries: list[Entry], ref: str) -> Entry | None:
    """The entry a `refutes:` line points at, by ref rather than by path.

    Matched inside the list the caller is already holding, not re-read from
    disk: reconcile mutates these objects, and a second copy of the same file
    would be deciding on a status the first copy has already changed.
    """
    by_ref = {entry.ref: entry for entry in entries}
    for candidate in _candidates(ref):
        found = by_ref.get(candidate)
        if found is not None:
            return found
    return None


def _refute(entries: list[Entry]) -> list[str]:
    """Retire the entries a later task went looking for and did not find.

    The mirror of the confirmation rule, and it needs no model either: a phase
    that hit an entry's exact situation and walked through it says so in its
    own entry, and this reads the two together. A refutation is one task's
    word, like any other entry — but it only ever *removes* a row, so the cost
    of a wrong one is a later phase debugging something it was warned about,
    not a later phase believing something false. That asymmetry is why this
    runs unattended and promotion does not.
    """
    refuted, held = [], []
    for entry in entries:
        if not entry.refutes:
            continue
        target = _match(entries, entry.refutes)
        if target is None or target.status == REFUTED:
            continue
        if target.task and target.task == entry.task:
            # A task correcting itself is a task changing its mind mid-run,
            # which is what editing the entry would have been. No evidence.
            continue
        if target.reviewed:
            # A human put it in the shared store on behalf of every project;
            # one task's counter-example is a reason to look, not a verdict.
            held.append(f"{entry.ref} -> {target.ref}")
            continue
        target.meta["status"] = REFUTED
        target.meta["refuted_by"] = entry.task or entry.ref
        _write(target)
        refuted.append(f"{target.ref} (by {entry.ref})")
    if refuted:
        logger.info("learnings: refuted by a later task: %s", ", ".join(refuted))
    if held:
        logger.info(
            "learnings: these refute an entry a human promoted, so they are left for "
            "`learnings --refute`: %s",
            ", ".join(held),
        )
    return refuted


def reconcile(hive_dir: str, harness: str = "") -> list[str]:
    """Confirm the entries a second task has now hit, and retire the dead ones.

    The poisoning guard: one phase can write anything, so an entry is a claim
    until something independent backs it. Two distinct tasks reporting the
    same error is that something, and it is checkable without a model — which
    is the point, since the alternative is paying a phase to review claims.

    The same pass is where entries leave: a refutation retires one outright,
    and an entry written under a different permission surface stops counting
    as evidence even though it keeps its row. Both are deliberately kept out
    of the corroboration map rather than deleted — a stale entry that a second
    task hits again under *this* harness is stamped with this harness and
    corroborates normally from then on.
    """
    entries = read_all(hive_dir)
    _refute(entries)
    live = [entry for entry in entries if entry.status != REFUTED and not entry.stale(harness)]
    seen = _tasks_by_fingerprint(live)
    confirmed = []
    for entry in live:
        if entry.reviewed or entry.status == CONFIRMED:
            continue
        if len(seen.get(entry.fingerprint, ())) < 2:
            continue
        entry.meta["status"] = CONFIRMED
        _write(entry)
        confirmed.append(entry.ref)
    if confirmed:
        logger.info("learnings: confirmed by a second task: %s", ", ".join(confirmed))
    stale = [entry.ref for entry in entries if entry.stale(harness)]
    if stale:
        logger.info(
            "learnings: written under a permission surface this run does not have, "
            "shown but not counted: %s",
            ", ".join(stale),
        )
    return confirmed


def stamp(hive_dir: str, task_id: str, harness: str) -> list[str]:
    """Record which harness this task's entries were written under.

    At the end of the task rather than as each file appears: the phases write
    these themselves, in containers, and asking them for one more frontmatter
    key they cannot compute would be a key they get wrong. Entries that
    already carry a stamp are left alone, so a task resumed under a changed
    config does not backdate its earlier findings.
    """
    if not harness:
        return []
    stamped = []
    for entry in read_inbox(hive_dir):
        if entry.task != task_id or entry.harness:
            continue
        entry.meta["harness"] = harness
        _write(entry)
        stamped.append(entry.ref)
    if stamped:
        logger.info(
            "task %s: stamped inbox entries with harness %s: %s",
            task_id, harness, ", ".join(stamped),
        )
    return stamped


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


def _free_path(path: str) -> str:
    """`path`, or the first `-2`, `-3`, ... beside it that nothing holds.

    Two tasks are free to file the same filename, and the second one landing
    must not overwrite the first one's copy — that overwrite is exactly the
    permanent loss the directory this is used for exists to stop.
    """
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    nth = 2
    while os.path.exists(f"{stem}-{nth}{ext}"):
        nth += 1
    return f"{stem}-{nth}{ext}"


def droppable(hive_dir: str, task_id: str) -> list[Entry]:
    """The inbox entries this task's merge claims it has promoted.

    Lifted out of `drop_promoted` so that the drop and the line `merge-task`
    prints when the gate keeps them read the same selector. `drop_promoted`
    answers with a `list[str]` of refs and nothing else — `docs/decisions.md`
    ADR 33 froze that — so a gated call answers `[]`, and `[]` cannot say what
    stayed: the message has to select the entries for itself. One selector
    means the count it prints can never disagree with the count that would have
    moved.
    """
    return [
        entry
        for entry in read_inbox(hive_dir)
        if not entry.reviewed
        and entry.scope == SCOPE_PROJECT
        and task_id in (entry.task, entry.carried_by)
    ]


def drop_promoted(hive_dir: str, task_id: str) -> list[str]:
    """Take the entries whose branch just merged out of the inbox.

    They are in the project's `docs/learnings/` now, committed, so keeping
    them here would charge every later phase of every task for a row it can
    already read in the repo. They move to `dropped/` rather than being
    unlinked, because the premise only holds where an auditor ran: at
    `merge-task`, on a cycle that died before one, the "other copy" the logged
    ref points at is a file that was never written (`docs/debt/T-012-D1.md`).
    No reader enumerates `dropped/`, so the prompt cost is gone either way —
    what stops is the loss being permanent.

    And the premise is checked rather than assumed: nothing moves unless the
    task card carries the promoting role's own section, which is the local
    record that the phase which files these entries returned. It is a proxy —
    an auditor that ran and ran out of turns before filing one entry still
    drops, and only checking each ref against the merged tree answers that —
    and it is the proxy for the case that has actually fired, a cycle that
    never reached an auditor at all (`docs/decisions.md` ADR 34).
    """
    entries = droppable(hive_dir, task_id)
    # `entries and`: a merge that filed nothing reads no card, so the ordinary
    # case costs what it cost before the gate existed.
    if entries and not context_transfer.has_phase_section(
        hive_dir, task_id, PROMOTING_ROLE
    ):
        logger.info(
            "task %s merged with no `## %s` section in its card: kept %d inbox entr(y/ies)",
            task_id, PROMOTING_ROLE, len(entries),
        )
        return []
    dropped = []
    for entry in entries:
        # Made here rather than in `ensure_dirs`: nothing writes into it but
        # this function, and an empty directory beside the two a phase is told
        # to use is one more thing for a phase to wonder about.
        os.makedirs(dropped_dir(hive_dir), exist_ok=True)
        target = _free_path(os.path.join(dropped_dir(hive_dir), os.path.basename(entry.path)))
        os.replace(entry.path, target)
        dropped.append(entry.ref)
    if dropped:
        logger.info(
            "task %s merged: moved inbox entries to %s/: %s",
            task_id, DROPPED_NAME, ", ".join(dropped),
        )
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
    seen = _tasks_by_fingerprint(
        [entry for entry in entries + read_harness(hive_dir) if entry.status != REFUTED]
    )
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


def _candidates(ref: str) -> list[str]:
    """Every ref that means this entry, for a human typing and an agent citing.

    The backticks come off because the table renders the ref in them and a
    phase copying a row out of its own prompt copies them too.
    """
    ref = (ref or "").strip().strip("`").strip()
    if not ref:
        return []
    return [ref, f"{ref}.md",
            os.path.join(INBOX_NAME, ref), os.path.join(INBOX_NAME, f"{ref}.md"),
            os.path.join(HARNESS_NAME, ref), os.path.join(HARNESS_NAME, f"{ref}.md")]


def resolve(hive_dir: str, ref: str) -> Entry | None:
    """One entry, by anything a human would type at it."""
    root = root_dir(hive_dir)
    for candidate in _candidates(ref):
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


def table(entries: list[Entry], harness: str = "") -> str:
    """The rows, as the index everywhere else in the harness renders them.

    `harness` is the permission surface being rendered against; without one
    nothing is stale, which is what the human listing wants when it is asked
    about a hive it is not currently running.
    """
    # Confirmed first, so that a table cut off at MAX_ROWS keeps the entries
    # something independent has already backed, and refuted last so those are
    # the first rows to fall off the end; stale before fresh within each band,
    # for the same reason. Ref last, so the order is stable between two phases
    # of the same task.
    ordered = sorted(
        entries,
        key=lambda e: (_STATUS_ORDER.get(e.status, 1), e.stale(harness), e.ref),
    )
    rows = [
        f"| `{entry.ref}` | {_cell(entry.rule)} | {_cell(entry.when)} | "
        f"{entry.status + ' (stale)' if entry.stale(harness) else entry.status} |"
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
    "marked unconfirmed was reported once and never reproduced — read it, do not trust it. "
    "One marked `(stale)` was written when this harness gave phases different permissions "
    "than you have now, so it may describe a wall that is no longer there: check it against "
    "what you can actually do before you plan around it."
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
    "chose not to finish is debt, and that goes in your handoff instead.\n\n"
    "If a row in the table below is wrong — you were in exactly the situation it describes "
    "and the trap did not bite — do not edit or delete its file. Write your own entry the "
    "same way, with the evidence that it did not bite, and add `refutes: <the ref from the "
    "table, without the backticks>` to your frontmatter. That retires the old entry and "
    "keeps both halves on disk; editing someone else's file just loses the argument."
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


def duties(
    role: str, task_id: str, project: str, hive_dir: str, harness: str = ""
) -> str:
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
    # Refuted rows are gone from the phases' table but still on disk and still
    # in the human's listing: the point of retiring one is to stop it costing
    # turns, not to hide that it was ever written.
    rows = [
        entry for entry in applicable(read_all(hive_dir), project)
        if entry.status != REFUTED
    ]
    if rows:
        parts.append(
            f"Entries that apply here, by file under `{root}/`:\n\n{table(rows, harness)}"
        )
        if role == "auditor":
            parts.append(_AUDITOR.format(task_id=task_id))
    return "\n\n".join(parts)
