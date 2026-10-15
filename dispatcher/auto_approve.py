# dispatcher/auto_approve.py
"""The automatic approve: whether a finished task may merge without a person.

`docs/plans/autonomy.md` *Decisions*, 1, and `docs/decisions.md` ADR 77. It
runs where `merge_on_done` runs, after the auditor, for a project that turned
it on (`auto_approve`), and approves only when every condition holds:

- `gates` — the last implementador round's gates ran and found nothing to
  block or ask about;
- `revisor` — the revisor approved, with no finding and no debt sent back;
- `auditor` — the auditor finished (`complete`) with nothing pending and no
  debt of its own, and no debt was filed for this task this cycle;
- `pre-merge` — the pre-merge check of *Decisions*, 2 passed;
- `surface` — the task's *Surface* section names paths, the changed paths
  could be read, and every one is inside the surface;
- `protected` — no changed path matches the protected list;
- `authorized` — the cycle was started by a person's authorization.

This module is the check alone, a pure function over what the cycle already
has: it runs nothing and reads nothing. Every condition is evaluated, so a
refusal names every one that failed rather than the first.
"""
from __future__ import annotations

import dataclasses

from dispatcher import gates as gates_mod
from dispatcher import surface

#: The heading of the comment either outcome writes on the task.
HEADING = "## automatic approve"

#: The condition names, in the order the comment lists them.
GATES = "gates"
REVISOR = "revisor"
AUDITOR = "auditor"
PRE_MERGE = "pre-merge"
SURFACE = "surface"
PROTECTED = "protected"
AUTHORIZED = "authorized"
CONDITIONS = (GATES, REVISOR, AUDITOR, PRE_MERGE, SURFACE, PROTECTED, AUTHORIZED)

#: The paths no automatic approve ever merges a change to, whatever the
#: project, as surface entries (`surface.covers` semantics: a literal is
#: root-anchored and holds what is under it, `**/` matches no directory too).
#: `.env*` is matched anywhere in the tree rather than only at the root: an
#: env file one directory down holds the same secrets.
FIXED_PROTECTED: tuple[str, ...] = (
    "docs/charter.md",
    "**/.env*",
    "config.yaml",
    "docker/compose/**",
    ".data/**",
)


@dataclasses.dataclass(frozen=True)
class Inputs:
    """What the cycle knows when the auditor is done, as the check reads it."""

    #: The last implementador round's gate report; None when no gate ran.
    gates: gates_mod.Report | None
    revisor_approved: bool
    revisor_findings: tuple[str, ...]
    #: Debt the revisor rejected or read as a block: either is work open.
    revisor_debt_open: tuple[str, ...]
    #: The auditor's handoff status, lowercased; None for a prose return.
    auditor_status: str | None
    auditor_pending: tuple[str, ...]
    #: The debt the auditor itself declared.
    auditor_debt: tuple[str, ...]
    #: The ids of the debt filed for this task this cycle.
    filed_debt: tuple[str, ...]
    pre_merge_passed: bool
    pre_merge_detail: str
    surface: frozenset[str]
    #: The paths the task branch changes against its target; None when they
    #: could not be read.
    changed_paths: tuple[str, ...] | None
    protected: tuple[str, ...]
    authorized: bool


@dataclasses.dataclass(frozen=True)
class Decision:
    """The check's answer: approved, or the conditions that failed and why."""

    #: (condition, why) for each condition that failed, in `CONDITIONS` order.
    failed: tuple[tuple[str, str], ...]
    #: (condition, what was checked) for each condition that held.
    held: tuple[tuple[str, str], ...]

    @property
    def approved(self) -> bool:
        return not self.failed

    @property
    def failed_conditions(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.failed)


def protected_patterns(
    projects_root: str,
    slug: str,
    project_protected: list[str] | tuple[str, ...] = (),
    credential_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """The protected list for one project: the fixed one, the credential
    paths that fall inside its repo, and its own `protected_paths`.

    A credential path is a path inside the container; one outside the
    project's checkout cannot be changed by a task branch, so only one under
    `<projects_root>/<slug>/` is kept, made repo-relative.
    """
    patterns = list(FIXED_PROTECTED)
    root = f"{projects_root.rstrip('/')}/{slug}/"
    for path in credential_paths:
        if path and path.startswith(root) and path[len(root):].strip("/"):
            patterns.append(path[len(root):].rstrip("/"))
    for path in project_protected:
        while path.startswith("./"):
            path = path[2:]
        if path:
            patterns.append(path)
    return tuple(dict.fromkeys(patterns))


def is_protected(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether `path` matches any protected pattern."""
    return any(surface.covers(frozenset({pattern}), path) for pattern in patterns if pattern)


def _gates(inputs: Inputs) -> tuple[bool, str]:
    if inputs.gates is None:
        return False, "the gates did not run on the last round (gates_enabled off, or a gate run that did not finish)"
    raised = [
        f"`{f.gate}` {f.detail}"
        for f in inputs.gates.findings
        if f.level in (gates_mod.BLOCKING, gates_mod.ASK)
    ]
    if raised:
        return False, "the gates raised: " + "; ".join(raised)
    return True, "every gate green on the last round"


def _revisor(inputs: Inputs) -> tuple[bool, str]:
    problems = []
    if not inputs.revisor_approved:
        problems.append("the revisor did not approve")
    if inputs.revisor_findings:
        problems.append("open finding(s): " + "; ".join(inputs.revisor_findings))
    if inputs.revisor_debt_open:
        problems.append("debt sent back: " + "; ".join(inputs.revisor_debt_open))
    if problems:
        return False, "; ".join(problems)
    return True, "the revisor approved with no open finding"


def _auditor(inputs: Inputs) -> tuple[bool, str]:
    problems = []
    if inputs.auditor_status is None:
        problems.append("the auditor returned no structured handoff to read")
    elif inputs.auditor_status != "complete":
        problems.append(f"the auditor ended `{inputs.auditor_status}`")
    if inputs.auditor_pending:
        problems.append("the auditor left pending: " + "; ".join(inputs.auditor_pending))
    if inputs.auditor_debt:
        problems.append("the auditor declared debt: " + "; ".join(inputs.auditor_debt))
    if inputs.filed_debt:
        problems.append("debt filed this cycle: " + ", ".join(inputs.filed_debt))
    if problems:
        return False, "; ".join(problems)
    return True, "the auditor finished with no finding and no debt filed this cycle"


def _pre_merge(inputs: Inputs) -> tuple[bool, str]:
    if not inputs.pre_merge_passed:
        return False, f"the pre-merge check did not pass: {inputs.pre_merge_detail}"
    return True, inputs.pre_merge_detail


def _surface(inputs: Inputs) -> tuple[bool, str]:
    if surface.is_whole_repo(inputs.surface):
        return False, "the task's Surface section names no path, so it is the whole repo"
    if inputs.changed_paths is None:
        return False, "the changed paths could not be read"
    out = surface.outside(inputs.surface, list(inputs.changed_paths))
    if out:
        return False, "changed outside the surface: " + ", ".join(out)
    return True, (
        f"{len(inputs.changed_paths)} changed path(s) inside the surface "
        f"({', '.join(sorted(inputs.surface))})"
    )


def _protected(inputs: Inputs) -> tuple[bool, str]:
    if inputs.changed_paths is None:
        return False, "the changed paths could not be read"
    hit = [path for path in inputs.changed_paths if is_protected(path, inputs.protected)]
    if hit:
        return False, "changed a protected path: " + ", ".join(hit)
    return True, "no changed path is protected"


def _authorized(inputs: Inputs) -> tuple[bool, str]:
    if not inputs.authorized:
        return False, "the cycle was not started by a person's authorization"
    return True, "the cycle was started by a person's authorization (run-task)"


_CHECKS = {
    GATES: _gates,
    REVISOR: _revisor,
    AUDITOR: _auditor,
    PRE_MERGE: _pre_merge,
    SURFACE: _surface,
    PROTECTED: _protected,
    AUTHORIZED: _authorized,
}


def decide(inputs: Inputs) -> Decision:
    """Every condition, evaluated; approved only when none failed."""
    failed, held = [], []
    for name in CONDITIONS:
        ok, why = _CHECKS[name](inputs)
        (held if ok else failed).append((name, why))
    return Decision(failed=tuple(failed), held=tuple(held))


def refusal_note(decision: Decision) -> str:
    """The comment on a task the check left unmerged."""
    lines = "\n".join(f"- `{name}`: {why}" for name, why in decision.failed)
    return (
        f"{HEADING}\n\n"
        f"Not merged: {len(decision.failed)} condition(s) failed. The task is done and "
        "its branch is kept; `dispatch merge-task` merges it by hand.\n\n"
        f"{lines}"
    )


def approval_note(decision: Decision, target: str, merge_commit: str | None) -> str:
    """The comment on a task the check approved and the merge landed."""
    lines = "\n".join(f"- `{name}`: {why}" for name, why in decision.held)
    commit = merge_commit or "unknown"
    return (
        f"{HEADING}\n\n"
        f"Approved and merged into {target} at {commit}. Every condition held:\n\n"
        f"{lines}"
    )


def merge_failed_note(decision: Decision, detail: str) -> str:
    """The comment on a task the check approved but whose merge did not land."""
    return (
        f"{HEADING}\n\n"
        f"Every condition held ({', '.join(name for name, _ in decision.held)}), "
        f"but the merge did not land: {detail}. The task is done and its branch is "
        "kept; `dispatch merge-task` merges it by hand."
    )
