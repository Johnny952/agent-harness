"""The base merge before an implementador in every cycle path (docs/decisions.md ADR 339).

The real `run_task_cycle` runs here, with its phases and the git around them
stubbed: what is read is whether the merge happened before the implementador
when the cycle is reached from the worker's request for changes, the worker's
ungrouped start and the CLI `run-task`, none of which passes a scheduler.
"""

from __future__ import annotations

import sys
import types

import pytest

from dispatcher import actions, cli as cli_mod, tasks, worker as worker_mod
from dispatcher import dispatcher as core
from tests.dispatcher.test_parallel_queue import _Phases
from tests.dispatcher.test_worker import (  # noqa: F401 - fixtures used by name
    accounts,
    cfg,
    clock,
    request_changes,
    start,
    worker,
)

_MOVE = core.BaseMove(changed=("dispatcher/queue.py",), merged_tasks=("T-0",))


class _Order(_Phases):
    """`_Phases` that also sees the base merge, so the order of the two is readable."""

    def merge(self, cfg, task_id, slug):
        self.calls.append((task_id, "merge", {}))
        return _MOVE

    def roles(self):
        return [role for _, role, _ in self.calls]


@pytest.fixture
def cycle(cfg, monkeypatch):
    """The real `run_task_cycle` with everything around its phases stubbed.

    Named `cycle` because the worker fixture asks for one; `gates` is what
    its teardown reads.
    """
    phases = _Order()
    phases.gates = {}

    def open_cycle(cfg, task_id, slug, description=None, project_lock=None, via="cycle"):
        return types.SimpleNamespace(
            cfg=cfg, task_id=task_id, slug=slug, held=False, foreign_lock=False,
            may_start_phase=None, completed=False, write_window=False,
            budget_reached=False, budget_spent=None,
            description=tasks.read_description(cfg.hive_tasks_dir, task_id) or description or "",
        )

    monkeypatch.setattr(core, "open_cycle", open_cycle)
    monkeypatch.setattr(core, "close_cycle", lambda ctx, completed: None)
    monkeypatch.setattr(core, "_mark_blocked", lambda ctx, reason=None: None)
    monkeypatch.setattr(core, "update_project_branch", lambda cfg, slug: None)
    monkeypatch.setattr(core, "_abort_leftover_merge", lambda cfg, task_id, slug: None)
    monkeypatch.setattr(core, "_needs_mapping", lambda cfg, slug: False)
    monkeypatch.setattr(core, "merge_base_at_write_window", phases.merge)
    monkeypatch.setattr(core, "run_phase", phases)
    return phases


def _merged_before_the_implementador(phases: _Order, task_id: str) -> None:
    roles = [role for task, role, _ in phases.calls if task == task_id]
    assert "implementador" in roles, roles
    assert roles.count("merge") == 1
    assert roles.index("merge") < roles.index("implementador")


def test_a_request_for_changes_rerun_merges_the_base_before_the_implementador(cfg, worker, cycle):
    request_changes(cfg)
    worker.tick()
    worker.join()

    _merged_before_the_implementador(cycle, "T-1")
    [(_, kwargs)] = cycle.of("implementador")
    assert "`dispatcher/queue.py`" in kwargs["note"]


def test_an_ungrouped_worker_start_merges_the_base_before_the_implementador(cfg, worker, cycle):
    start(cfg, "T-1")
    worker.tick()
    worker.join()

    _merged_before_the_implementador(cycle, "T-1")


def test_the_cli_run_task_merges_the_base_before_the_implementador(cfg, cycle, monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "accounts:\n  - name: cuenta1\n    container: agent-cuenta1\n"
        f"hive_tasks_dir: {cfg.hive_tasks_dir}\nstate_dir: {cfg.state_dir}\n"
        f"projects_root: {cfg.projects_root}\n"
    )
    description = "## Goal\n\nDo it.\n\n## Surface\n\n`dispatcher/`\n\n## Done when\n\nIt is done.\n"
    monkeypatch.setattr(cli_mod, "load_config", lambda path: cfg)
    monkeypatch.setattr(sys, "argv", [
        "ia-harness-dispatcher", "--config", str(config_path),
        "run-task", "--task-id", "T-1", "--project", "proj", "--description", description,
    ])
    with pytest.raises(SystemExit):
        cli_mod.main()

    _merged_before_the_implementador(cycle, "T-1")
