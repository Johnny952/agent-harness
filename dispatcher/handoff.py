# dispatcher/handoff.py
"""The shape of what a phase hands to the next one.

Every phase used to return free prose, which `run_task_cycle` truncated blind
and appended to the task file, so each later role reread every earlier one and
paid for the middle of a long return that had already been cut away. Here the
return is a fixed set of short fields instead, requested through the CLI's
`--json-schema`, rendered into the task file by the dispatcher, and kept under
a per-role byte budget the dispatcher enforces itself.

The fields are the ones a later phase actually needs: what changed, what was
verified, what is still open, what might bite, which subagents exist and what
each was doing, what deserves to become a learning or a debt entry, and where
the detail lives. The detail itself never travels — it goes to files and the
handoff cites their paths.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from dispatcher import debt, docker_exec

logger = logging.getLogger(__name__)

#: The only status the cycle acts on. The other two are for whoever reads
#: the task file; this one ends the task before the next phase runs.
BLOCKED = "blocked"

_STATUS_VALUES = ("complete", "partial", BLOCKED)

APPROVED = "APPROVED"
CHANGES_REQUESTED = "CHANGES_REQUESTED"

# Descriptions ride along on every call the role makes, so they are terse on
# purpose: enough to say what belongs in the field, not a style guide.
_PROPERTIES: dict[str, dict] = {
    "status": {
        "type": "string",
        "enum": list(_STATUS_VALUES),
        "description": "complete if the phase finished its job, partial if some of it is left, blocked if it could not.",
    },
    "changed": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What this phase changed, one line each, by path or symbol. Empty for a phase that only read.",
    },
    "verified": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What you checked and how, one line each (command run, test that passed). Not what you believe.",
    },
    "pending": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What the next phase still has to do.",
    },
    "risks": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What could bite later: assumptions made, edges not covered, anything you had to guess.",
    },
    "subagents": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The subagent's raw id."},
                "doing": {"type": "string", "description": "What it was working on."},
            },
            "required": ["id", "doing"],
            "additionalProperties": False,
        },
        "description": (
            "Subagents you started, as a record of who was on what. Leave it empty: the "
            "dispatcher fills the ids in from disk, because the Agent tool tells you not "
            "to repeat them."
        ),
    },
    "learnings": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Proposed learnings: something true of this project that the next task would want to know.",
    },
    "debt": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "enum": list(debt.ORIGINS),
                    "description": (
                        f"{debt.INTRODUCED} if this task created it; {debt.FOUND} if it was "
                        "already there, in a file this task touched and not already in the "
                        "debt index."
                    ),
                },
                "what": {"type": "string", "description": "The work that was not done."},
                "where": {
                    "type": "string",
                    "description": (
                        "Where it bites, as a condition a later task can check against its "
                        "own work — not a topic."
                    ),
                },
                "why": {"type": "string", "description": "Why it stays: what made leaving it right here."},
                "cost": {"type": "string", "description": "What leaving it costs, and to whom."},
                "fix": {
                    "type": "string",
                    "description": (
                        "What would resolve it, or the doc section that already decides how."
                    ),
                },
            },
            "required": ["origin", "what", "where", "why", "cost", "fix"],
            "additionalProperties": False,
        },
        "description": (
            "Proposed debt: work you deliberately did not do. Never a substitute for "
            "blocking — what would block the task still blocks it."
        ),
    },
    "paths": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file, plus a heading or symbol if it is long."},
                "holds": {"type": "string", "description": "What is in it."},
            },
            "required": ["path", "holds"],
            "additionalProperties": False,
        },
        "description": "Where the detail is. Cite by path and heading, never by line number.",
    },
}

_VERDICT_PROPERTY = {
    "verdict": {
        "type": "string",
        "enum": [APPROVED, CHANGES_REQUESTED],
        "description": f"{APPROVED} only when there are no blocking findings; {CHANGES_REQUESTED} otherwise.",
    },
}

#: The revisor's other ruling. Debt is proposed by the phase that created it
#: and filed by a phase that did not see it happen, so the ruling in between is
#: the only place anyone asks whether leaving the work undone was a choice or
#: an excuse.
_DEBT_RULINGS_PROPERTY = {
    "debt_rulings": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "debt": {
                    "type": "string",
                    "description": "The declaration you are ruling on, as its `what` states it.",
                },
                "ruling": {
                    "type": "string",
                    "enum": list(debt.RULINGS),
                    "description": (
                        f"{debt.ACCEPTED}: it gets filed in the debt index. "
                        f"{debt.REJECTED}: it is a finding, fix it this round. "
                        f"{debt.BLOCKS}: it is not debt, it is a block wearing a debt costume. "
                        f"{debt.FOLLOW_UP}: it is not debt, it is the next task; it is filed "
                        "as a backlog item that depends on this one."
                    ),
                },
                "why": {"type": "string", "description": "The reason for the ruling, in one line."},
            },
            "required": ["debt", "ruling", "why"],
            "additionalProperties": False,
        },
        "description": (
            "One ruling per declaration in the implementation handoff. Empty when it "
            "declared none."
        ),
    },
}

#: What sends the round back, as lines the dispatcher can compare between
#: reviews. A finding the next review still holds is copied, not reworded, so
#: "the same reason as last time" is string equality on a fingerprint rather
#: than a judgement about prose (`docs/decisions.md` ADR 73).
_FINDINGS_PROPERTY = {
    "findings": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "What sends this round back, one line each. Empty when approving. "
            "One an earlier review raised that still holds: copy its line verbatim."
        ),
    },
}

#: The other half of the debt flow, and the implementador's own: the entries
#: this task made go away. It is a claim by the phase that did the work, the
#: same way the verdict is a claim by the phase that reviewed it.
_RESOLVED_DEBT_PROPERTY = {
    "resolved_debt": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Ids of debt entries this task resolved, as the debt index writes them "
            "(T-001-D1). Empty when it resolved none."
        ),
    },
}

#: The arquitecto's other way to end a task early: not "this cannot be done"
#: but "this is more than one task". Only the arquitecto's extras carry it.
#: Offering the field is the switch — a role that can return a split will —
#: so it joined `_ROLE_EXTRAS` together with the dispatcher branch that files
#: one (`docs/plans/task-split.md`, section 8, step 4; `docs/decisions.md`
#: ADR 60).
_SPLIT_PROPERTY = {
    "split": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The sub-task in one line."},
                "surface": {
                    "type": "string",
                    "description": "The one surface it changes: an api route, a screen, a region of a screen, or one module's contract.",
                },
                "description": {
                    "type": "string",
                    "description": "The sub-task as its own task description: what to build, what it reads from earlier sub-tasks, and when it is done.",
                },
                "after": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based positions of earlier entries in this list that must be merged first. Empty for none.",
                },
            },
            "required": ["title", "surface", "description", "after"],
            "additionalProperties": False,
        },
        "description": "Empty unless the task spans more than one surface. Then: the sub-tasks in the order they run, and no plan.",
    },
}

#: What a phase may file at once; entries past it are refused, not dropped.
_COMMAND_REQUESTS_MAX = 3
_TARGET_KINDS = ("agent", "dind", "service")

#: A role's way out of a refused command: not a workaround, but a request the
#: operator rules on (`docs/plans/command-requests.md`, decision 2;
#: `docs/decisions.md` ADR 117). Only the roles that run commands in the
#: task's tree carry it, the implementador and the cartografo: the revisor
#: and the auditor read and report, and the arquitecto plans. `name` is in
#: every target so the schema stays all-required; it only means something
#: for a service.
_COMMAND_REQUESTS_PROPERTY = {
    "command_requests": {
        "type": "array",
        "maxItems": _COMMAND_REQUESTS_MAX,
        "items": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "The command as an argument vector, no shell.",
                },
                "reason": {"type": "string", "description": "What the command is for, in one line."},
                "target": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(_TARGET_KINDS)},
                        "name": {"type": "string", "description": "The service, when kind is service. Empty otherwise."},
                    },
                    "required": ["kind", "name"],
                    "additionalProperties": False,
                },
                "cwd": {"type": "string", "description": "Relative to the task's worktree; . for its root."},
            },
            "required": ["argv", "reason", "target", "cwd"],
            "additionalProperties": False,
        },
        "description": "Empty unless a command you need was denied. Then: that command, and end the phase.",
    },
}

#: The integrador's report (ADR 277): how it resolved each file it was given,
#: so the revisor and the person read the resolution's intent and not only its
#: diff; and, when the two sides disagree on the design rather than on lines,
#: why it stopped. A design conflict is not its to settle: it ends `blocked`
#: with this reason, touches nothing, and the person answers with request
#: changes.
_INTEGRADOR_PROPERTY = {
    "resolutions": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "A file you were given, relative to the worktree root."},
                "how": {"type": "string", "description": "What each side wanted there and what the resolution keeps, in one line."},
            },
            "required": ["path", "how"],
            "additionalProperties": False,
        },
        "description": "One entry per file you resolved. Empty when blocked.",
    },
    "blocked_reason": {
        "type": "string",
        "description": "Empty unless status is blocked. Then: the design decision the two sides disagree on, in one or two lines.",
    },
}

#: The roles that hand off at all. A role outside this gets no schema, which
#: means no `--json-schema` flag and the free-text fallback below — the same
#: way an unknown role gets no skills rather than an error.
_ROLE_EXTRAS: dict[str, dict] = {
    "cartografo": _COMMAND_REQUESTS_PROPERTY,
    "arquitecto": _SPLIT_PROPERTY,
    "implementador": {**_RESOLVED_DEBT_PROPERTY, **_COMMAND_REQUESTS_PROPERTY},
    "revisor": {**_VERDICT_PROPERTY, **_FINDINGS_PROPERTY, **_DEBT_RULINGS_PROPERTY},
    "auditor": {},
    "integrador": _INTEGRADOR_PROPERTY,
}

#: What one entry costs: a line a later phase reads, plus the quoting and the
#: comma the JSON puts around it. Was 128, the mean. Every accepted return in
#: `.data/verify/` was measured back into the JSON the dispatcher counted, and
#: an entry costs a median of 130 bytes net of the envelope, a mean of 132 and
#: a p90 of 161. A budget divided by the mean is one half the handoffs are
#: over by construction, which is what the runs show: the roles came in a
#: median of two entries *under* the count they were given and eight of the
#: thirty-two still went over the bytes. This is the p90, so a role that
#: spends its entries fits its budget about nine times in ten.
_LINE_BYTES = 160

#: The JSON around the lines: every key, every empty array, the status string.
#: Was 256. Measured with every field empty it is 123 bytes for the roles with
#: the common fields alone, 142 for the implementador and 162 for the revisor,
#: which pays for a verdict and a ruling list.
_ENVELOPE_BYTES = 160

#: Bytes of JSON a role's handoff may spend. Sized off what a phase delivers,
#: not off what it was asked for: every accepted return across eight tasks was
#: parsed back into its canonical JSON and priced per entry, and each budget
#: below is the entries that role actually filed on T-008 at the p90 density
#: plus the envelope. T-008 is the first task against a repo that is not the
#: toy one, and it is the evidence these numbers are for: five phases out of
#: five went over on the first attempt and three were still over after the one
#: rewrite they are allowed.
#:
#: What made them go over is volume, not prose. T-008 ran 144 bytes an entry,
#: mid-range for the toy tasks' 114 to 158, and filed 169 entries against
#: their 60 to 135. The detail did go to files — some 30 KB of notes under the
#: task's scratch dir, with the `paths` counts unchanged — and a real repo
#: still has more to report per phase than a toy one does.
_BUDGET_BYTES: dict[str, int] = {
    # The mapper's output is the docs it wrote, not its handoff: what the next
    # phase needs from it is a pointer to the index and how far it got. No run
    # has exercised it yet, so this one is still a guess; it moves only to keep
    # the entry count it derives worth stating. Plus 768 for the command
    # requests it may file (ADR 117): three, at about 250 bytes each.
    "cartografo": 3328,
    # Filed 32 entries on T-008 against the 30 it was given, and measured 4732
    # against a 4096 budget. Its entries run to a p90 of 161 bytes.
    "arquitecto": 5120,
    # The one role that pays for two structured lists on top of the common
    # fields, and the densest of them at 185 bytes an entry on the p90: a debt
    # declaration alone measures a median of 465, nearly three of these lines.
    # Filed 40 entries on T-008 and measured 6294 against 5120. Plus 768 for
    # the command requests it may file (ADR 117): three, at about 250 bytes
    # each, so filing them does not cost the report it ends the phase with.
    "implementador": 7936,
    # It carries a verdict and a ruling per declaration on top of the common
    # fields, and ran the only phase that went over twice: 4028 on the first
    # round and 4280 on the second, both against 4096. Its entries are the
    # cheapest of the structured roles at a p90 of 137.
    "revisor": 5120,
    # The phase that reports on the whole task and commits the docs it files.
    # Its first draft on T-008 measured 4443 against 3584; what it kept after
    # the rewrite was 27 entries, which is what this pays for at its p90 of
    # 126 bytes an entry.
    "auditor": 4608,
    # Not measured: no run has resolved a conflict yet. Its work is the files
    # it was given, and its report is a line per file plus the common fields;
    # half the default holds a dozen files and keeps the phase short, which a
    # conflict resolution should be (ADR 277).
    "integrador": 2048,
}

_DEFAULT_BUDGET_BYTES = 4096

#: How far over a role may go before the dispatcher spends a call on it. A
#: `--resume` costs about what a small phase costs, so it only pays when it
#: buys back something a later phase would otherwise have had to read: over
#: four runs, four of the eleven overages were 46, 67, 170 and 280 bytes —
#: a line and a half each, for the price of a model call.
_SHRINK_MARGIN = 0.10
_SHRINK_MARGIN_FLOOR = 256


def schema_for(role: str) -> dict | None:
    """The JSON Schema a role's return is validated against, or None."""
    extras = _ROLE_EXTRAS.get(role)
    if extras is None:
        return None
    properties = {**_PROPERTIES, **extras}
    # Every field is required, with an empty array when there is nothing to
    # say. An optional field invites the model to drop the ones it has least
    # to report on — which are exactly the ones whose emptiness is a fact the
    # next phase wants (no subagents left running, nothing unverified).
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def budget_for(role: str) -> int:
    return _BUDGET_BYTES.get(role, _DEFAULT_BUDGET_BYTES)


def lines_for(role: str) -> int:
    """The same budget in the unit the role can count while it writes.

    A model cannot measure its own output in bytes, which is most of why the
    byte budget was missed in every run that logged one. Entries it can count,
    and an entry is what the fields hold anyway: the dispatcher asks for this
    number and checks the other one, and they cannot drift because this one is
    derived.
    """
    return max(1, (budget_for(role) - _ENVELOPE_BYTES) // _LINE_BYTES)


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def parse(result: docker_exec.ClaudeResult) -> dict | None:
    """The structured return, or None when this phase did not produce one.

    The CLI puts a validated return in `structured_output` on the result
    envelope, and leaves `result` holding whatever the model said around it.
    The text is still worth a look because a phase run without a schema (an
    unknown role, an older image) can only answer in text.
    """
    payload = (result.raw or {}).get("structured_output")
    if isinstance(payload, dict):
        return payload
    try:
        candidate = json.loads(_strip_fence(result.result_text or ""))
    except (json.JSONDecodeError, TypeError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def measure(result: docker_exec.ClaudeResult) -> int:
    """Bytes this return costs the handoff.

    A structured return is measured as its canonical JSON rather than as the
    text around it: the JSON is the part that lands in the task file, and it
    is the part the role can be asked to shorten.
    """
    payload = parse(result)
    text = _canonical(payload) if payload is not None else (result.result_text or "")
    return len(text.encode("utf-8"))


def over_budget(role: str, result: docker_exec.ClaudeResult) -> int:
    """Bytes over this role's budget, 0 when it fits."""
    return max(0, measure(result) - budget_for(role))


def worth_shrinking(role: str, overage: int) -> bool:
    """Whether an overage is big enough to be worth a `--resume`.

    The budget is the editorial line and the retry is the editing pass, but
    the pass is not free. An overage inside the margin is taken as it comes:
    the task file is a line longer and nobody notices, which is the cheaper of
    the two ways to be wrong here.
    """
    return overage >= max(_SHRINK_MARGIN_FLOOR, int(budget_for(role) * _SHRINK_MARGIN))


#: Appended to every phase's system prompt that has a handoff schema. Under
#: `-p` a turn that ends on a progress report ends the phase, which Anthropic's
#: guide to Opus 5.5 warns it may do when run unattended
#: (`docs/plans/models-5-5.md`, *Decisions*, 3). Never sent to the main thread,
#: where a person answers and a turn is meant to end.
UNATTENDED_PHASE = (
    "This phase runs unattended: nobody reads your turns while it runs and "
    "nobody can answer a question. It ends only when you send the structured "
    "handoff your schema describes, and a turn that ends any other way ends the "
    "phase with nothing handed on. So do not stop to report progress, to list "
    "what is left, to ask whether to go on, or to wait for a confirmation that "
    "will not come. If you cannot go on, say why in the handoff and send it."
)


#: Sent to the roles whose schema carries `command_requests` (ADR 117). The
#: workaround is what this forbids: a denied command retried under another
#: name, or the job done another way, is the call the operator never saw.
COMMAND_REQUESTS_PROMPT = (
    "When a command you need is denied, do not work around it: no other tool, "
    "no other spelling, no doing its job by hand. File it in `command_requests` "
    "(at most 3, argv with no shell, cwd relative to the worktree) and end the "
    "phase with the handoff; the operator decides and the task resumes."
)


def continue_prompt() -> str:
    """The `--resume` a phase gets when it ended without its handoff."""
    return (
        "The phase is not over: it ends only when you send the structured handoff. "
        "Carry on from where you stopped, and send it when the work is done or "
        "when you cannot go further."
    )


def shrink_prompt(role: str, size: int, budget: int) -> str:
    """The one `--resume` a phase gets when its handoff blew the budget."""
    return (
        f"That handoff was {size} bytes; a {role} handoff has to fit in {budget}. "
        "Send the same structured return again, shorter. Keep every field and "
        "keep status and verdict exactly as they were — this is a rewrite of the "
        "wording, not of the findings. Cut each entry to one line, and move "
        "anything longer into a file, cited under `paths` by path and heading."
    )


def discarded_writes_prompt(role: str, paths: list[str]) -> str:
    """The one `--resume` a reviewing phase gets when it wrote to its worktree.

    A reviewing checkout is detached and rebuilt every round
    (docker_exec.create_worktree), and nothing in it is ever committed, so an
    edit made there is gone the moment the round ends and the phase is never
    told. This is the telling. The edit is lost either way; what the phase
    still decides is whether the next phase hears about it at all.
    """
    listed = ", ".join(paths[:10])
    if "verdict" in _ROLE_EXTRAS.get(role, {}):
        correction = (
            f"Return `verdict: {CHANGES_REQUESTED}` with the change you wanted "
            "described under `findings`, precisely enough that the phase owning the "
            "branch can make it without you."
        )
    else:
        correction = (
            "Describe the change you wanted under `findings`, precisely enough that "
            "the phase owning the branch can make it without you."
        )
    return (
        "Your checkout is detached and throwaway: it is deleted when this round ends "
        "and nothing in it is committed, so what you changed there never reaches the "
        f"task branch — {listed}. It is already lost. Do not try to save it, and do "
        "not report it as work you did. "
        f"{correction} "
        "Send the same structured return again with that correction and every other "
        "field as it was."
    )


def verdict_of(payload: dict | None) -> str | None:
    """The revisor's verdict as the schema carries it, or None."""
    if not payload:
        return None
    verdict = payload.get("verdict")
    if not isinstance(verdict, str):
        return None
    return verdict.strip().upper() or None


def blocked(payload: dict | None) -> bool:
    """Whether the phase said it could not do its job.

    The field has been in the schema from the beginning and nothing read it:
    `render` printed it into the task file and the cycle ran the next phase
    anyway. Reading it is deliberately narrow — one role, one branch in
    `run_task_cycle` — because a status is cheap for a role to set, and every
    place that acts on one is a place a role can end a task from.
    """
    if not payload:
        return False
    status = payload.get("status")
    if not isinstance(status, str):
        return False
    return status.strip().lower() == BLOCKED


def pending(payload: dict | None) -> list[str]:
    """What the phase left for the next one, as it stated it.

    On a blocked phase this is the list of things that have to be decided
    before the task can run again, which is why the block is worth reading:
    a status with nothing under it names no way out.
    """
    if not payload:
        return []
    return _lines(payload, "pending")


def findings_of(payload: dict | None) -> list[str]:
    """What the revisor said sends the round back, one line each."""
    if not payload:
        return []
    return _lines(payload, "findings")


@dataclass(frozen=True)
class SubTask:
    """One entry of a split, as the arquitecto stated it.

    `after` holds 1-based positions in the list `split_of` returned. On a
    payload the CLI validated those are integers; on one that came back
    through the prose fallback they are whatever was there, kept so that
    `split_problems` refuses them.
    """

    title: str
    surface: str
    description: str
    after: tuple[int, ...]


#: A one-entry split is a plan with extra steps; more than four means the task
#: was a project, and cutting that is the operator's call, not a phase's.
_SPLIT_MIN = 2
_SPLIT_MAX = 4


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def split_of(payload: dict | None) -> list[SubTask]:
    """The sub-tasks the phase split its task into, empty when it did not.

    Reads, never repairs. An entry that is not an object is dropped, the way
    `_lines` drops what it cannot print; anything else unreadable is kept
    blank or as stated, so `split_problems` refuses the split rather than a
    sub-task being filed without the `after` it was given.
    """
    if not payload:
        return []
    entries = payload.get("split")
    if not isinstance(entries, list):
        return []
    subtasks = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        after = entry.get("after")
        if after is None:
            after = ()
        elif not isinstance(after, list):
            after = (after,)
        subtasks.append(SubTask(
            title=_text(entry.get("title")).strip(),
            surface=_text(entry.get("surface")).strip(),
            # Verbatim: it becomes the sub-task's own task description.
            description=_text(entry.get("description")),
            after=tuple(after),
        ))
    return subtasks


def split_problems(subtasks: list[SubTask]) -> list[str]:
    """What makes a split unusable, one line each. Empty means it can be filed.

    Every problem is reported, not the first: a bad split ends the task as a
    block, and the block's reasons are all the arquitecto's next run gets.
    `after` may only name entries before its own, which is what makes a cycle
    impossible without anyone walking the graph.
    """
    problems = []
    if not _SPLIT_MIN <= len(subtasks) <= _SPLIT_MAX:
        problems.append(
            f"a split has {_SPLIT_MIN} to {_SPLIT_MAX} sub-tasks, this one has {len(subtasks)}"
        )
    for position, subtask in enumerate(subtasks, start=1):
        for field in ("title", "surface", "description"):
            value = getattr(subtask, field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"sub-task {position}: {field} is blank")
        # `type() is int`, so True is not position 1. Kept in a list and not a
        # set: nothing says a junk item is hashable.
        seen: list[int] = []
        repeated: list[int] = []
        for item in subtask.after:
            if type(item) is not int:
                problems.append(
                    f"sub-task {position}: after names {item!r}, which is not an integer"
                )
            elif item in seen:
                if item not in repeated:
                    repeated.append(item)
                    problems.append(f"sub-task {position}: after names {item} more than once")
            else:
                seen.append(item)
                if not 1 <= item < position:
                    problems.append(
                        f"sub-task {position}: after names {item}, which is not an entry before it"
                    )
    return problems


#: The `blocked_reason` a task file gets when a phase filed a command request.
COMMAND_REQUEST = "command_request"


def command_request_problems(entry: object) -> list[str]:
    """What makes one command request unusable. Empty means it is accepted.

    Every problem is reported, like `split_problems`: the refusal is all the
    operator and the next run get to see of what the phase asked for.
    """
    if not isinstance(entry, dict):
        return [f"the request is not an object but {type(entry).__name__}"]
    problems = []
    argv = entry.get("argv")
    if not isinstance(argv, list) or not argv:
        problems.append("argv is not a non-empty list")
    elif not all(isinstance(item, str) and item for item in argv):
        problems.append("argv has an item that is not a non-empty string")
    if not isinstance(entry.get("reason"), str):
        problems.append("reason is not a string")
    target = entry.get("target")
    kind = target.get("kind") if isinstance(target, dict) else None
    if kind not in _TARGET_KINDS:
        problems.append(f"target kind is {kind!r}, not one of {', '.join(_TARGET_KINDS)}")
    elif kind == "service":
        name = target.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append("a service target has no name")
    cwd = entry.get("cwd")
    if not isinstance(cwd, str):
        problems.append("cwd is not a string")
    elif cwd.startswith("/"):
        problems.append(f"cwd {cwd!r} is absolute")
    elif ".." in cwd.split("/"):
        problems.append(f"cwd {cwd!r} has a .. segment")
    return problems


def command_requests(payload: dict | None) -> tuple[list[dict], list[dict]]:
    """The phase's command requests, as accepted and refused.

    Reads, never repairs, and never drops: an entry is either accepted as
    stated or refused as `{"request": entry, "reason": ...}`. A missing field,
    or one that is not a list, is no requests at all.
    """
    if not payload:
        return [], []
    entries = payload.get("command_requests")
    if not isinstance(entries, list):
        return [], []
    accepted: list[dict] = []
    refused: list[dict] = []
    for position, entry in enumerate(entries, start=1):
        if position > _COMMAND_REQUESTS_MAX:
            problems = [f"over the limit of {_COMMAND_REQUESTS_MAX} requests"]
        else:
            problems = command_request_problems(entry)
        if problems:
            refused.append({"request": entry, "reason": "; ".join(problems)})
        else:
            accepted.append(entry)
    return accepted, refused


def files_command_requests(role: str) -> bool:
    """Whether the role's schema offers `command_requests` at all."""
    return "command_requests" in _ROLE_EXTRAS.get(role, {})


def fallback_body(text: str, head: int = 500, tail: int = 1500) -> str:
    """Clamp a return that came back as prose instead of the schema.

    Nothing routine lands here any more: it is the phase that ignored the
    schema, or ran on an image whose CLI did not have one. The tail is kept,
    not just the head, because a revisor that answered in prose closes with
    its verdict line and `revisor_approved` still reads it from there.
    """
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[… {omitted} chars omitted …]\n\n{text[-tail:]}"


def _lines(payload: dict, key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _pairs(payload: dict, key: str, first: str, second: str) -> list[str]:
    rendered = []
    for item in payload.get(key) or []:
        if not isinstance(item, dict):
            continue
        left = str(item.get(first, "")).strip()
        right = str(item.get(second, "")).strip()
        if left:
            rendered.append(f"`{left}` — {right}" if right else f"`{left}`")
    return rendered


_SECTIONS = (
    ("findings", "Findings"),
    ("changed", "Changed"),
    ("verified", "Verified"),
    ("pending", "Pending"),
    ("risks", "Risks"),
    ("learnings", "Proposed learnings"),
    ("resolved_debt", "Resolved debt"),
)


def _debt(payload: dict) -> list[str]:
    """Each declaration on one line, since the handoff is read as prose.

    The fields are kept separate in the schema because the dispatcher and the
    auditor read them separately; here they collapse, because the phase
    reading this wants to know what was left undone, not to parse it.
    """
    rendered = []
    for item in payload.get("debt") or []:
        if not isinstance(item, dict):
            continue
        what = str(item.get("what", "")).strip()
        if not what:
            continue
        origin = str(item.get("origin", "")).strip()
        head = f"{what} ({origin})" if origin else what
        tail = " · ".join(
            f"{label}: {value}"
            for label, value in (
                ("where", str(item.get("where", "")).strip()),
                ("why", str(item.get("why", "")).strip()),
                ("cost", str(item.get("cost", "")).strip()),
                ("fix", str(item.get("fix", "")).strip()),
            )
            if value
        )
        rendered.append(f"{head} — {tail}" if tail else head)
    return rendered


def _rulings(payload: dict) -> list[str]:
    """The revisor's call on each declaration, so the auditor files the right ones."""
    rendered = []
    for item in payload.get("debt_rulings") or []:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("debt", "")).strip()
        if not subject:
            continue
        ruling = str(item.get("ruling", "")).strip() or "?"
        why = str(item.get("why", "")).strip()
        line = f"**{ruling}** — {subject}"
        rendered.append(f"{line} ({why})" if why else line)
    return rendered


def _split(payload: dict) -> list[str]:
    """Each sub-task on one numbered line, without its description.

    The description goes to the sub-task's own file, where its roles read it;
    here it would be paid for by every later reader of the parent. Whitespace
    is collapsed because a title is the model's text: a newline in one would
    put whatever follows it at column zero of the task file, where `## <role>`
    is a phase heading (see `body`).
    """
    rendered = []
    for position, subtask in enumerate(split_of(payload), start=1):
        pieces = [f"{position}.", subtask.title]
        if subtask.surface:
            pieces.append(f"({subtask.surface})")
        if subtask.after:
            pieces.append("— after " + ", ".join(str(item) for item in subtask.after))
        rendered.append(" ".join(" ".join(pieces).split()))
    return rendered


def _command_requests(payload: dict) -> list[str]:
    """Each request on one line: the command, where it runs, and why.

    Whitespace is collapsed for the reason `_split` gives: an argv item is the
    model's text, and a newline in one would reach column zero.
    """
    accepted, refused = command_requests(payload)
    rendered = []
    for entry in accepted:
        target = entry["target"]
        where = f"service {target['name']}" if target["kind"] == "service" else target["kind"]
        line = f"`{' '.join(entry['argv'])}` on {where} in `{entry['cwd']}` — {entry['reason']}"
        rendered.append(" ".join(line.split()))
    for item in refused:
        rendered.append(" ".join(f"refused: {item['reason']}".split()))
    return rendered


def render(payload: dict) -> str:
    """The structured return as the markdown a later role reads.

    Empty sections are dropped rather than printed empty: the next role pays
    for every line of this, and "Risks: (none)" costs the same as a risk.
    """
    head = []
    status = str(payload.get("status", "")).strip()
    if status:
        head.append(f"**Status:** {status}")
    verdict = verdict_of(payload)
    if verdict:
        head.append(f"**Verdict:** {verdict}")
    parts = [" · ".join(head)] if head else []

    # Right under the status, like the verdict: it is the phase's outcome, and
    # the lines carry their own numbers instead of a `- `.
    split = _split(payload)
    if split:
        parts.append("\n".join(["**Split**", *split]))

    for key, title in _SECTIONS:
        items = _lines(payload, key)
        if items:
            parts.append("\n".join([f"**{title}**", *(f"- {item}" for item in items)]))

    for key, title, items in (
        ("debt", "Proposed debt", _debt(payload)),
        ("debt_rulings", "Debt rulings", _rulings(payload)),
        ("command_requests", "Command requests", _command_requests(payload)),
    ):
        if items:
            parts.append("\n".join([f"**{title}**", *(f"- {item}" for item in items)]))

    subagents = _pairs(payload, "subagents", "id", "doing")
    if subagents:
        parts.append("\n".join(["**Subagents**", *(f"- {item}" for item in subagents)]))

    paths = _pairs(payload, "paths", "path", "holds")
    if paths:
        parts.append("\n".join(["**Detail**", *(f"- {item}" for item in paths)]))

    resolutions = _pairs(payload, "resolutions", "path", "how")
    if resolutions:
        parts.append("\n".join(["**Resolutions**", *(f"- {item}" for item in resolutions)]))

    reason = str(payload.get("blocked_reason") or "").strip()
    if reason:
        parts.append(f"**Blocked:** {reason}")

    return "\n\n".join(parts)


def body(label: str, text: str, payload: dict | None) -> str:
    """What `handoff()` appends to the task file for one phase."""
    rendered = render(payload) if payload else ""
    if not rendered:
        # Either no schema came back, or one came back with every field empty:
        # a phase that says nothing at all is worse than its prose, so fall
        # back to the prose rather than append a bare heading. Indented, so no
        # line of it reaches column zero: a `## <role>` heading in the task
        # file is what `context_transfer.has_phase_section` takes as proof the
        # role's phase returned, and only the f-string below may write one.
        rendered = _indent(fallback_body(text or ""))
    return f"## {label}\n\n{rendered}"


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" if line.strip() else "" for line in text.splitlines())
