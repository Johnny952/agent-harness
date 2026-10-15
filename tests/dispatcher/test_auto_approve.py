"""The automatic approve's check, alone (`dispatcher/auto_approve.py`, ADR 77)."""
import dataclasses

import pytest

from dispatcher import auto_approve as aa
from dispatcher import gates

_SURFACE = frozenset({"src/app.py", "tests/"})
_PROTECTED = aa.protected_patterns("/data/projects", "myproj")


def _inputs(**overrides) -> aa.Inputs:
    """Every condition holds; each test breaks one."""
    base = dict(
        gates=gates.Report([gates.Finding(gates.LINT, "style nit", gates.NOTE)]),
        revisor_approved=True,
        revisor_findings=(),
        revisor_debt_open=(),
        auditor_status="complete",
        auditor_pending=(),
        auditor_debt=(),
        filed_debt=(),
        pre_merge_passed=True,
        pre_merge_detail="agent/task/task-1 already holds main",
        surface=_SURFACE,
        changed_paths=("src/app.py", "tests/test_app.py", "docs/decisions.md"),
        protected=_PROTECTED,
        authorized=True,
    )
    base.update(overrides)
    return aa.Inputs(**base)


def test_every_condition_holding_approves() -> None:
    decision = aa.decide(_inputs())

    assert decision.approved
    assert decision.failed == ()
    assert [name for name, _ in decision.held] == list(aa.CONDITIONS)


@pytest.mark.parametrize(
    ("condition", "overrides"),
    [
        (aa.GATES, dict(gates=None)),
        (aa.GATES, dict(gates=gates.Report([gates.Finding(gates.TESTS_RUN, "2 failed", gates.BLOCKING)]))),
        (aa.GATES, dict(gates=gates.Report([gates.Finding(gates.TESTS_IN_DIFF, "no test", gates.ASK)]))),
        (aa.REVISOR, dict(revisor_approved=False)),
        (aa.REVISOR, dict(revisor_findings=("the endpoint is untested",))),
        (aa.REVISOR, dict(revisor_debt_open=("cache the lookup",))),
        (aa.AUDITOR, dict(auditor_status=None)),
        (aa.AUDITOR, dict(auditor_status="partial")),
        (aa.AUDITOR, dict(auditor_pending=("update the README",))),
        (aa.AUDITOR, dict(auditor_debt=("the index is stale",))),
        (aa.AUDITOR, dict(filed_debt=("D-0007",))),
        (aa.PRE_MERGE, dict(pre_merge_passed=False, pre_merge_detail="conflicts in src/app.py")),
        (aa.SURFACE, dict(changed_paths=("src/app.py", "src/other.py"))),
        (aa.SURFACE, dict(surface=frozenset(), changed_paths=("src/app.py",))),
        (aa.PROTECTED, dict(surface=frozenset({"src/", ".env"}), changed_paths=("src/.env.local",))),
        (aa.PROTECTED, dict(surface=frozenset({"config.yaml"}), changed_paths=("config.yaml",))),
        (aa.AUTHORIZED, dict(authorized=False)),
    ],
)
def test_one_condition_failing_alone_refuses_naming_it(condition, overrides) -> None:
    decision = aa.decide(_inputs(**overrides))

    assert not decision.approved
    assert decision.failed_conditions == (condition,)
    note = aa.refusal_note(decision)
    assert note.startswith(aa.HEADING)
    assert f"`{condition}`" in note


def test_unreadable_changed_paths_fail_surface_and_protected_never_pass() -> None:
    """None is not an empty change: it would otherwise pass both."""
    decision = aa.decide(_inputs(changed_paths=None))

    assert decision.failed_conditions == (aa.SURFACE, aa.PROTECTED)


def test_every_failed_condition_is_named_not_only_the_first() -> None:
    decision = aa.decide(_inputs(gates=None, authorized=False, auditor_status="blocked"))

    assert decision.failed_conditions == (aa.GATES, aa.AUDITOR, aa.AUTHORIZED)


def test_the_append_only_logs_are_never_outside_the_surface() -> None:
    decision = aa.decide(_inputs(changed_paths=("docs/ROADMAP.md", "docs/learnings/x.md")))

    assert decision.approved


@pytest.mark.parametrize(
    "path",
    ["docs/charter.md", ".env", "app/.env.production", "config.yaml",
     "docker/compose/pool.yaml", ".data/projects/x"],
)
def test_the_fixed_list_is_protected(path) -> None:
    assert aa.is_protected(path, _PROTECTED)


@pytest.mark.parametrize(
    "path", ["docs/charter.md.bak", "app/config.yaml", "docker/Dockerfile", "src/env.py"],
)
def test_neighbours_of_the_fixed_list_are_not_protected(path) -> None:
    assert not aa.is_protected(path, _PROTECTED)


def test_protected_patterns_add_credential_paths_inside_the_repo_and_the_projects_own() -> None:
    patterns = aa.protected_patterns(
        "/data/projects", "myproj",
        ["./migrations/", "secrets/**"],
        ("/data/projects/myproj/.git-credentials", "/run/secrets/projects", "", "/data/projects/other/k"),
    )

    assert patterns[: len(aa.FIXED_PROTECTED)] == aa.FIXED_PROTECTED
    assert ".git-credentials" in patterns
    assert "migrations/" in patterns and "secrets/**" in patterns
    assert not any(p.startswith("/") for p in patterns)
    assert aa.is_protected("migrations/0001.sql", patterns)
    assert aa.is_protected("secrets/a/b", patterns)


def test_the_approval_note_lists_every_condition_and_the_merge_commit() -> None:
    decision = aa.decide(_inputs())

    note = aa.approval_note(decision, "main", "c" * 40)

    assert note.startswith(aa.HEADING)
    assert "main" in note and "c" * 40 in note
    for name in aa.CONDITIONS:
        assert f"`{name}`" in note


def test_the_merge_failed_note_says_the_conditions_held_and_why_it_did_not_land() -> None:
    note = aa.merge_failed_note(aa.decide(_inputs()), "the project has uncommitted changes")

    assert note.startswith(aa.HEADING)
    assert "the project has uncommitted changes" in note
    assert "merge-task" in note


def test_inputs_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _inputs().authorized = False
