# dispatcher/project_docs.py
"""The docs a project keeps for the agents that work on it.

A project used to arrive with whatever its humans had written, and every task
re-read the source to find out what the project was. The alternative is not a
hand-written `CLAUDE.md` per project — nobody keeps one current — but docs the
roles write as a side effect of doing the work, committed with the code so a
re-clone still has them.

This module owns three things about those docs: where they live, what each
role owes them, and how the dispatcher reads the little of it that has to be
machine-readable. The layout below is the whole contract — a path that is not
a constant here is a path no role was told to write.

The docs outrank the skills and any `CLAUDE.md` about this project: the skills
describe method, the docs describe *this* codebase. Where they disagree about
the project's own domain, the project wins.
"""
from __future__ import annotations

import logging

import yaml

from dispatcher import docker_exec

logger = logging.getLogger(__name__)

#: Everything below is under here: one name so that a project that moves its
#: docs moves them in one place.
DOCS_DIR = "docs"
#: The index, and what its absence means: a project nobody has mapped yet.
INDEX = f"{DOCS_DIR}/README.md"
#: The one doc in here a human writes and no role may edit: the rulings a task
#: is given rather than allowed to re-decide. Everything else under DOCS_DIR is
#: agent-authored, which is exactly why this one is separate — every file a role
#: is told to write, a role will write.
CHARTER = f"{DOCS_DIR}/charter.md"
#: Numbered ADRs, appended to and struck through, never rewritten.
DECISIONS = f"{DOCS_DIR}/decisions.md"
ARCHITECTURE = f"{DOCS_DIR}/architecture.md"
BUSINESS = f"{DOCS_DIR}/business.md"
LEARNINGS_DIR = f"{DOCS_DIR}/learnings"
LEARNINGS_INDEX = f"{LEARNINGS_DIR}/README.md"
DEBT_DIR = f"{DOCS_DIR}/debt"
DEBT_INDEX = f"{DEBT_DIR}/README.md"
IMPLEMENTATIONS_DIR = f"{DOCS_DIR}/implementations"

#: The docs whose sentences are claims about the tree as it is now: an index of
#: what exists, the rulings in force, the architecture, the business, the debt
#: still open. A pointer in one of these is broken the moment what it names
#: moves, and whoever moved it is the one who can fix it — so a gate reads
#: these whole, every task, whether the task touched them or not.
PRESENT_DOCS = (
    INDEX, CHARTER, ARCHITECTURE, BUSINESS, LEARNINGS_INDEX, DEBT_DIR,
)

#: The docs that record what was true when they were written, and go stale by
#: design: DECISIONS is appended to and never rewritten, an implementation note
#: describes a branch that has already landed, a learning names the file it was
#: learned in. This project's own rule for a record that disagrees with the
#: tree is to leave the sentence alone and record the disagreement somewhere
#: newer (`docs/learnings/a-plans-present-tense-claim-is-a-citation.md`), so
#: the only lines in here anybody owes an answer for are the ones a task has
#: just added.
RECORD_DOCS = (DECISIONS, IMPLEMENTATIONS_DIR, LEARNINGS_DIR)


def _named_by(path: str, group: tuple[str, ...]) -> bool:
    """Whether the layout names this path, as a file or inside a directory.

    A constant naming a file can never match the second test, so one loop
    reads both kinds and no caller has to know which constants are directories.
    """
    return any(
        path == entry or path.startswith(f"{entry}/") for entry in group
    )


def is_record(path: str) -> bool:
    """Whether this doc is a record rather than a claim about the present.

    PRESENT_DOCS wins where the two overlap: LEARNINGS_INDEX sits inside
    LEARNINGS_DIR and is the one file in there about the present, its rows
    saying which learnings exist right now. False is not the opposite claim —
    a path under DOCS_DIR that neither group names is a doc no role was told
    to write, and nobody owes anything for it either way.
    """
    return not _named_by(path, PRESENT_DOCS) and _named_by(path, RECORD_DOCS)


#: The role of the mapping phase: it writes the index a project arrives
#: without, and never touches the code.
MAPPER_ROLE = "cartografo"

#: What each role's commit is allowed to stage, for the roles that need
#: holding to it. The auditor is one of the writers (docker_exec.WRITER_ROLES)
#: so its indexes survive the task, but it runs after the revisor has already
#: approved: a commit taking the whole tree would put anything else it touched
#: on the branch with nobody having read it. Everything it is asked to write
#: is under DOCS_DIR, which is what makes the scope exactly the duty.
_COMMIT_SCOPES = {"auditor": (DOCS_DIR,)}

#: What no role's commit stages, whatever its scope says. The scope above is
#: DOCS_DIR and CHARTER is under it, so without this the auditor — the one role
#: whose whole duty is writing docs — is also the one role that could quietly
#: rewrite the ruling it was given. The charter's own "When an entry is wrong"
#: gives a role three ways to report a ruling it cannot satisfy, and editing
#: the entry is not one of them: this is that paragraph made true of the
#: commit rather than left to the prompt.
_COMMIT_EXCLUDES = (CHARTER,)


def commit_scope(role: str) -> tuple[str, ...] | None:
    """The paths this role may commit, or None for the whole worktree.

    None is the default because most roles are asked for the work itself, and
    the work has no fixed shape to hold them to.
    """
    return _COMMIT_SCOPES.get(role)


def commit_excludes() -> tuple[str, ...]:
    """The paths no role's commit stages, whoever the role is.

    Not keyed by role, unlike the scope: "no role may edit it" is the whole
    point of the charter, and a mapping invites an exception.
    """
    return _COMMIT_EXCLUDES


def implementation_doc(task_id: str) -> str:
    """Where one task records how it was built."""
    return f"{IMPLEMENTATIONS_DIR}/{task_id}.md"


def index_path(project_dir: str) -> str:
    return f"{project_dir}/{INDEX}"


def has_index(container: str, project_dir: str) -> bool:
    """Whether this project has been mapped.

    Asked of the project's own checkout rather than of a task branch: the
    checkout is the merged state, which is what a *later* task will see. A map
    that only exists on an unmerged task branch reads as missing here, and the
    next task maps again — the cost of `merge_on_done: false`, and one more
    reason the mapping phase is opt-in.
    """
    return docker_exec.path_exists(container, index_path(project_dir))


_FRONTMATTER_DELIM = "---"


def _frontmatter(text: str) -> dict:
    if not text.lstrip().startswith(_FRONTMATTER_DELIM):
        return {}
    try:
        _, fm_text, _ = text.split(_FRONTMATTER_DELIM, 2)
        loaded = yaml.safe_load(fm_text)
    except (ValueError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


#: The keys the index's frontmatter is asked for. Everything else in these
#: docs is prose for a model to read; these two are for the dispatcher, which
#: has to run them with no model in the loop at all.
COMMAND_KEYS = ("build", "test")


def read_commands(container: str, project_dir: str) -> dict[str, str]:
    """The project's own build and test commands, as the index records them.

    They live in YAML frontmatter at the top of the index, because the gate
    that runs the tests runs through `docker exec` without `claude`: a command
    a model has to find in prose is a command the gate cannot run.
    """
    proc = docker_exec.run_docker_exec(container, project_dir, ["cat", INDEX])
    if proc.returncode != 0:
        return {}
    fm = _frontmatter(proc.stdout)
    commands = {}
    for key in COMMAND_KEYS:
        value = fm.get(key)
        if isinstance(value, str) and value.strip():
            commands[key] = value.strip()
    return commands


# Every fragment below rides on one call of one role, so they are written
# tight: what to write, where, and the one rule that keeps it usable. They are
# instructions about *this project's* docs, which is why they are here and not
# in a vendored skill — a skill describes method and travels between projects.

_ANCHORS = (
    "Cite what you point at by path plus a stable anchor — an ADR number, an entry id, a "
    "heading, a symbol name — never by line number: lines move with the next commit and the "
    "pointer silently starts lying."
)

_ARQUITECTO = (
    "This project's docs are in `docs/`, and about this project they outrank your skills and "
    f"any CLAUDE.md. Start at `{INDEX}` and follow it: the learnings and debt indexes carry a "
    "trigger per row saying when the entry applies and where it bites, so read the index whole "
    f"and open only the entries whose trigger matches this task. `{CHARTER}`, if it exists, is "
    "the one doc a human wrote and no role may edit: read its triggers the same way and treat "
    "the entries that match as given, not as something to weigh. A charter entry you cannot "
    "satisfy is reported — in `risks` if the task can still be finished, as a block if it "
    "cannot — and never edited or worked around. When the task decides something "
    f"a later task could undo without knowing it was a decision, append an ADR to `{DECISIONS}`: "
    "the next number, the context, the decision, its consequences, and a status. Never rewrite "
    "an ADR that is already there — to replace one, strike its heading through and point at the "
    "number that supersedes it.\n\n"
    "If the task cannot be specified from what you were given, set `status` to `blocked` and put "
    "each missing definition on its own line in `pending`. That ends the task here, before the "
    "implementador runs, which is the point: an implementador handed an underspecified task does "
    "not stop, it invents the missing decision, and the review that follows reviews the invention. "
    "Block for a decision nobody has made, not for a problem that is merely hard — anything you can "
    "decide yourself and record as an ADR is yours to decide. Work a user sees is where this bites "
    "most often: if the task changes a screen and no plan or charter entry says what that screen "
    "shows, how it behaves with no data, with stale data or when the call fails, those are missing "
    "definitions and not details to fill in. Name each one concretely enough that one answer "
    "unblocks it."
)

_IMPLEMENTADOR = (
    "Record how you built it in `{implementation_doc}` — what you did, why this way, and what "
    "you ruled out — and update any doc whose contract you changed. Anything true of this project "
    "that the next task would want to know goes in `learnings`.\n\n"
    "Work you deliberately did not do goes in `debt`, one entry each, with all six fields: "
    "`origin`, `introduced` if this task created the debt and `found` if it was already there; "
    "`what` it is; `where` — the condition a later task can check against its own work to know "
    "this bites it, not a topic; `why` it stays; the `cost` of leaving it; and `fix`, what would "
    f"actually resolve it. Declare found debt only in files this task touched and only if "
    f"`{DEBT_INDEX}` does not already carry it — the rest of the project's backlog is not this "
    "task's to re-declare. Declaring debt never replaces blocking: whatever would block this task, "
    "like a decision the task does not specify or a schema change, still blocks it.\n\n"
    f"If this task resolved debt `{DEBT_INDEX}` already carries, put those entry ids in "
    "`resolved_debt`. That is what closes the entry, and its card, when the branch lands.\n\n"
    "Learnings and debt are proposals, not files: the auditor is the only phase that writes the "
    "indexes."
)

_REVISOR = (
    "Your checkout is detached and thrown away when this round ends: nothing you write in it "
    "is committed and the branch never sees it. So do not fix what you find. A change you want "
    "is a finding, described closely enough that the implementador can make it without you.\n\n"
    "A contract that changed with no doc changed with it is a finding: a public signature, an "
    "API route, a migration, a config or env key, a CLI flag. So is a doc that has gone stale — "
    "an ADR or a learning the code no longer matches costs more than no doc at all, because the "
    "next phase will believe it.\n\n"
    "Rule on every debt the implementation declared: one entry in `debt_rulings` per declaration, "
    "restating it in `debt` closely enough to recognise which one you mean. `accepted` if leaving "
    "the work undone is a defensible call, `rejected` if it is work this task should simply have "
    "done, `blocks` if it is a block wearing a debt costume — something the task cannot decide for "
    "itself, like a decision it was never given or a schema change. A rejection is a finding like "
    "any other: it sends the task round again and counts against the revision rounds, so reject "
    "what has to be fixed now, not what you would have written differently. `blocks` ends the task "
    "blocked, with no further round. Debt you do not rule on is accepted."
)

_AUDITOR = (
    "You are the only phase that writes the indexes, which is what keeps two phases from editing "
    "them at once. Take the learnings the earlier phases proposed in this task's handoffs — plus "
    "any entry this task carries in the shared learnings inbox named below — and file them: a "
    f"learning becomes a file under `{LEARNINGS_DIR}/` and a row in `{LEARNINGS_INDEX}`.\n\n"
    "Debt works the other way round. You file only the entries this prompt hands you by id, "
    "because their cards are already on the board and the row has to point back at them; debt "
    f"nobody handed you is not yours to file. Each one becomes a file under `{DEBT_DIR}/` named "
    f"after its id, and a row in `{DEBT_INDEX}`. An entry the implementation says it resolved is "
    "marked resolved where it stands, with the task that resolved it, never deleted — a fix that "
    "gets reverted should still have its row.\n\n"
    "Every row carries its trigger — \"when it applies\" for a learning, \"where\" for a debt — "
    "written as a condition the next agent can check against its own task, not as a topic. Where "
    "a spec already decides how a debt gets fixed, point the entry at that section instead of "
    f"copying it. Keep `{INDEX}` pointing at what now exists. A business rule you inferred from "
    f"the code rather than read somewhere goes in `{BUSINESS}` marked unconfirmed, for a human to "
    "confirm or kill. Friction with your own skills is not a project doc: say it in `risks`."
)

_MAPPER = (
    "This project has no docs index, so nothing about it has been written down for the agents "
    "that come after you. Map it. Do not change its code, do not rewrite the docs it already "
    "has, and do not fix anything you find.\n\n"
    "{budget}Work outside-in — build files, entry points, directory names, tests — and write:\n"
    f"- `{INDEX}`, the index. It opens with YAML frontmatter holding `build:` and `test:`, each "
    "one command exactly as it is run from the project root, because later phases run those "
    "without a model in the loop; omit a key you could not establish rather than guessing one. "
    "Then: what this project is, its stack, its modules and what each is for, and a table of the "
    "docs that already exist with, per row, when an agent should open it. Point at those docs, "
    "do not summarise them.\n"
    f"- `{ARCHITECTURE}`: the shape. Modules, how they talk, what crosses a process or network "
    "boundary, and the contracts visible from outside — routes, CLI flags, env keys, schemas.\n"
    f"- `{BUSINESS}`: what the software is for and the rules it enforces, each marked confirmed "
    "(you read it in a doc or a comment) or unconfirmed (you inferred it from the code). "
    "Unconfirmed is not a failure — it is the list a human is being asked to confirm.\n\n"
    "Thin and true beats complete and late: if the budget runs out mid-map, stop where you are. "
    "What you wrote is committed, and the next task extends it."
)


def _budget_line(max_turns: int | None) -> str:
    if not max_turns:
        return ""
    return (
        f"You have {max_turns} turns and no more; the run is cut off there. Spend them on "
        "breadth, not depth: a reader who knows where to look beats one paragraph that is "
        "exhaustive.\n\n"
    )


def duties(role: str, task_id: str, max_turns: int | None = None) -> str:
    """What this role owes the project's docs, as a prompt fragment.

    Empty for a role with no docs duty, the same way an unknown role gets no
    skills: a new role added elsewhere degrades to saying nothing rather than
    to an error in the middle of a dispatch.
    """
    if role == MAPPER_ROLE:
        return f"{_MAPPER.format(budget=_budget_line(max_turns))}\n\n{_ANCHORS}"
    body = {
        "arquitecto": _ARQUITECTO,
        "implementador": _IMPLEMENTADOR.format(implementation_doc=implementation_doc(task_id)),
        "revisor": _REVISOR,
        "auditor": _AUDITOR,
    }.get(role)
    if body is None:
        return ""
    return f"{body}\n\n{_ANCHORS}"
