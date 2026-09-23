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
each was doing, what deserves to become a learning or a debt card, and where
the detail lives. The detail itself never travels — it goes to files and the
handoff cites their paths.
"""
from __future__ import annotations

import json
import logging

from dispatcher import debt, docker_exec

logger = logging.getLogger(__name__)

_STATUS_VALUES = ("complete", "partial", "blocked")

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
                        f"{debt.ACCEPTED}: it gets filed and put on the board. "
                        f"{debt.REJECTED}: it is a finding, fix it this round. "
                        f"{debt.BLOCKS}: it is not debt, it is a block wearing a debt costume."
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

#: The roles that hand off at all. A role outside this gets no schema, which
#: means no `--json-schema` flag and the free-text fallback below — the same
#: way an unknown role gets no skills rather than an error.
_ROLE_EXTRAS: dict[str, dict] = {
    "cartografo": {},
    "arquitecto": {},
    "implementador": _RESOLVED_DEBT_PROPERTY,
    "revisor": {**_VERDICT_PROPERTY, **_DEBT_RULINGS_PROPERTY},
    "auditor": {},
}

#: Bytes of JSON a role's handoff may spend. Guesses, deliberately: what makes
#: them tunable is that every overage is logged with the role and the size, so
#: the numbers can be moved from data rather than taste. The reviewing roles
#: get less because their detail belongs in files under the task's scratch
#: directory, not in a body every later phase rereads.
_BUDGET_BYTES: dict[str, int] = {
    # The mapper's output is the docs it wrote, not its handoff: what the next
    # phase needs from it is a pointer to the index and how far it got, so it
    # gets no more room than the tightest reviewing role.
    "cartografo": 2048,
    "arquitecto": 4096,
    # The one role that pays for two structured lists on top of the common
    # fields: a debt declaration is six fields, and several of them fit in a
    # round. shrink_prompt is still the backstop when they do not.
    "implementador": 5120,
    "revisor": 3072,
    "auditor": 2048,
}

_DEFAULT_BUDGET_BYTES = 4096


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


def shrink_prompt(role: str, size: int, budget: int) -> str:
    """The one `--resume` a phase gets when its handoff blew the budget."""
    return (
        f"That handoff was {size} bytes; a {role} handoff has to fit in {budget}. "
        "Send the same structured return again, shorter. Keep every field and "
        "keep status and verdict exactly as they were — this is a rewrite of the "
        "wording, not of the findings. Cut each entry to one line, and move "
        "anything longer into a file, cited under `paths` by path and heading."
    )


def verdict_of(payload: dict | None) -> str | None:
    """The revisor's verdict as the schema carries it, or None."""
    if not payload:
        return None
    verdict = payload.get("verdict")
    if not isinstance(verdict, str):
        return None
    return verdict.strip().upper() or None


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

    for key, title in _SECTIONS:
        items = _lines(payload, key)
        if items:
            parts.append("\n".join([f"**{title}**", *(f"- {item}" for item in items)]))

    for key, title, items in (
        ("debt", "Proposed debt", _debt(payload)),
        ("debt_rulings", "Debt rulings", _rulings(payload)),
    ):
        if items:
            parts.append("\n".join([f"**{title}**", *(f"- {item}" for item in items)]))

    subagents = _pairs(payload, "subagents", "id", "doing")
    if subagents:
        parts.append("\n".join(["**Subagents**", *(f"- {item}" for item in subagents)]))

    paths = _pairs(payload, "paths", "path", "holds")
    if paths:
        parts.append("\n".join(["**Detail**", *(f"- {item}" for item in paths)]))

    return "\n\n".join(parts)


def body(label: str, text: str, payload: dict | None) -> str:
    """What `handoff()` appends to the task file for one phase."""
    rendered = render(payload) if payload else ""
    if not rendered:
        # Either no schema came back, or one came back with every field empty:
        # a phase that says nothing at all is worse than its prose, so fall
        # back to the prose rather than append a bare heading.
        rendered = fallback_body(text or "")
    return f"## {label}\n\n{rendered}"
