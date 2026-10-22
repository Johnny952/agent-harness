"""Debt index entries as backlog items (T-086, ADR 131): the `impact` column,
`debt.backlog_item`, and `tasks.propose_debt` / `tasks.file_debt`."""

import threading
from pathlib import Path

import pytest

from dispatcher import debt, tasks
from dispatcher.context_transfer import TaskFile, read_task_file, task_file_path, write_task_file

_INDEX = """# Debt

| id | what | where | fix | card | impact |
|---|---|---|---|---|---|
| `T-1-D1` | The **retry** loop is untested | a task touching `cli.py` | a fake clock | none | risk |
| `T-1-D2` | The config loader guesses | a task adding a config key | read the schema | none | Cosmetic |
| `T-2-D1` | **Resolved 2026-10-01 by T-3.** A race | anything | a lock | none | none |
| `T-2-D2` | Unjudged | somewhere | something | none | urgent |
"""

_OLD_INDEX = """| id | what | where | fix | card |
|---|---|---|---|---|
| `T-1-D1` | An old row | here | that | card-aaa |
"""

_DESCRIPTION = "## Goal\n\nSomething.\n\n## Done when\n\nIt is done.\n"


def _hive(tmp_path: Path) -> str:
    hive = tmp_path / "hive" / "tasks"
    hive.mkdir(parents=True)
    return str(hive)


def _task(hive: str, task_id: str, status: str = "pending", **fields) -> None:
    write_task_file(
        task_file_path(hive, task_id),
        TaskFile(task_id=task_id, status=status, owner=None, depends_on=[], heartbeat=None,
                 body="", **fields),
    )
    tasks.write_description(hive, task_id, _DESCRIPTION)


def _read(hive: str, task_id: str) -> TaskFile:
    return read_task_file(task_file_path(hive, task_id))


def _row(entry: str = "T-1-D1", impact: str = "risk", **fields) -> dict:
    return {"id": entry, "what": "The retry loop is untested", "where": "cli.py",
            "fix": "a fake clock", "card": "none", "impact": impact, "resolved": False, **fields}


# --- the impact column -------------------------------------------------------


def test_impact_is_read_by_column_name_and_anything_else_is_none() -> None:
    rows = debt.index_rows(_INDEX)
    assert [(r["id"], r["impact"]) for r in rows] == [
        ("T-1-D1", "risk"), ("T-1-D2", "cosmetic"), ("T-2-D1", "none"), ("T-2-D2", "none"),
    ]
    assert {r["card"] for r in rows} == {"none"}


def test_a_row_written_before_the_column_has_no_impact_and_keeps_its_card() -> None:
    [row] = debt.index_rows(_OLD_INDEX)
    assert (row["card"], row["impact"]) == ("card-aaa", "none")


def test_a_five_cell_row_under_the_six_column_header_reads_as_no_impact() -> None:
    text = _INDEX + "| `T-3-D1` | Short row | here | that | none |\n"
    row = debt.index_rows(text)[-1]
    assert (row["id"], row["card"], row["impact"]) == ("T-3-D1", "none", "none")


@pytest.mark.parametrize("cell, impact", [
    ("risk", "risk"), (" COST ", "cost"), ("**cosmetic**", "cosmetic"),
    ("", "none"), ("none", "none"), ("high", "none"), (None, "none"),
])
def test_impact_of(cell, impact) -> None:
    assert debt.impact_of(cell) == impact


@pytest.mark.parametrize("impact, priority", [
    ("risk", "high"), ("cost", "normal"), ("cosmetic", "low"), ("none", "normal"),
])
def test_priority_for(impact, priority) -> None:
    assert debt.priority_for(impact) == priority


def test_every_mapped_priority_is_a_board_priority() -> None:
    assert set(debt.IMPACT_PRIORITY.values()) | {debt.DEFAULT_IMPACT_PRIORITY} <= set(
        tasks.PRIORITIES
    )


def test_open_rows_skip_resolved_and_unnumbered_rows() -> None:
    text = _INDEX + "| | No id | here | that | none | risk |\n"
    assert [r["id"] for r in debt.open_rows(text)] == ["T-1-D1", "T-1-D2", "T-2-D2"]


def test_the_filing_note_asks_for_an_impact() -> None:
    declaration = debt.Declaration(what="w", where="x", fix="y")
    note = debt.filing_note([("T-1-D1", declaration)])
    assert "impact" in note and "`risk`" in note and "`cost`" in note and "`cosmetic`" in note
    assert "id | what | where | fix | card | impact" in note


# --- backlog_item --------------------------------------------------------------


def test_backlog_item_builds_a_valid_proposal() -> None:
    title, description, priority = debt.backlog_item(debt.index_rows(_INDEX)[0])
    assert title == "T-1-D1: The retry loop is untested"
    assert priority == "high"
    assert description.startswith("## Goal\n\nThe retry loop is untested\n\nWhere: a task touching `cli.py`")
    assert "`docs/debt/T-1-D1.md`" in description
    assert description.endswith("## Done when\n\na fake clock\n")
    assert tasks._description_problems(description, "it") == []
    assert tasks._title_problems(title, "it") == []


def test_backlog_item_cuts_a_long_title_and_fills_an_empty_fix() -> None:
    title, description, _ = debt.backlog_item(_row(what="x " * 300, fix="", where=""))
    assert len(title) <= tasks.MAX_TITLE_CHARS and title.endswith("...")
    assert "Where:" not in description
    assert "has a resolution row in `docs/debt/README.md`" in description
    assert tasks._description_problems(description, "it") == []


# --- propose_debt --------------------------------------------------------------


def test_propose_debt_files_a_proposed_debt_item_at_the_end_of_its_bucket(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-007", priority="high", rank=5000)
    _task(hive, "T-008", priority="low", rank=99999)
    out = tasks.propose_debt(
        hive, debt_id="T-1-D1", title="T-1-D1: x", description=_DESCRIPTION, priority="high",
    )
    assert out == {"id": "T-009", "created": True}
    item = _read(hive, "T-009")
    assert (item.status, item.source, item.priority) == ("proposed", "debt", "high")
    assert (item.tags, item.debt_id, item.title) == (["debt"], "T-1-D1", "T-1-D1: x")
    assert item.rank == 5000 + tasks.RANK_STEP
    assert tasks.read_description(hive, "T-009") == _DESCRIPTION
    assert tasks.field_problems(item) == []


def test_propose_debt_is_idempotent_on_the_debt_id_at_any_status(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    first = tasks.propose_debt(
        hive, debt_id="T-1-D1", title="T-1-D1: x", description=_DESCRIPTION, priority="low",
    )
    tasks.accept(hive, first["id"], "dropped")
    again = tasks.propose_debt(
        hive, debt_id="T-1-D1", title="another title", description=_DESCRIPTION, priority="high",
    )
    assert again == {"id": first["id"], "created": False}
    assert sorted(tasks.read_all_tasks(hive)) == [first["id"]]
    assert _read(hive, first["id"]).status == "dropped"


def test_propose_debt_again_writes_a_lost_description(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    out = tasks.propose_debt(
        hive, debt_id="T-1-D1", title="T-1-D1: x", description=_DESCRIPTION, priority="normal",
    )
    Path(tasks.description_path(hive, out["id"])).unlink()
    tasks.propose_debt(
        hive, debt_id="T-1-D1", title="T-1-D1: x", description=_DESCRIPTION, priority="normal",
    )
    assert tasks.read_description(hive, out["id"]) == _DESCRIPTION


@pytest.mark.parametrize("fields, needle", [
    ({"debt_id": "T-1"}, "debt_id"),
    ({"title": ""}, "title"),
    ({"description": "no sections"}, "description"),
    ({"priority": "urgent"}, "priority"),
])
def test_propose_debt_refuses_and_writes_nothing(tmp_path: Path, fields, needle) -> None:
    hive = _hive(tmp_path)
    args = {"debt_id": "T-1-D1", "title": "t", "description": _DESCRIPTION, "priority": "normal"}
    with pytest.raises(tasks.BoardWriteError) as excinfo:
        tasks.propose_debt(hive, **{**args, **fields})
    assert needle in "; ".join(excinfo.value.problems)
    assert tasks.read_all_tasks(hive) == {}


def test_propose_debt_waits_for_the_create_lock(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    done = threading.Event()

    def run() -> None:
        tasks.propose_debt(
            hive, debt_id="T-1-D1", title="t", description=_DESCRIPTION, priority="normal",
        )
        done.set()

    with tasks.task_lock(hive, tasks.CREATE_LOCK):
        thread = threading.Thread(target=run)
        thread.start()
        assert not done.wait(0.3)
    thread.join(5)
    assert done.is_set()


@pytest.mark.parametrize("value, ok", [
    ("T-065-D1", True), ("task-1-D12", True), ("T-1", False), ("T-1-D", False),
    ("x y-D1", False), (7, False),
])
def test_a_debt_id_is_validated_on_read(tmp_path: Path, value, ok) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-1", status="proposed", debt_id=value)
    problems = tasks.field_problems(_read(hive, "T-1"))
    assert (problems == []) is ok
    if not ok:
        assert "debt_id" in problems[0]


def test_a_task_with_no_debt_id_has_none_and_round_trips(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-1")
    assert _read(hive, "T-1").debt_id is None
    assert "debt_id" not in Path(task_file_path(hive, "T-1")).read_text()


# --- file_debt -----------------------------------------------------------------


def test_file_debt_files_each_open_row_once(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    rows = debt.index_rows(_INDEX)
    first = tasks.file_debt(hive, rows)
    assert [(r["debt_id"], r["id"], r["created"], r["priority"]) for r in first] == [
        ("T-1-D1", "T-001", True, "high"),
        ("T-1-D2", "T-002", True, "low"),
        ("T-2-D2", "T-003", True, "normal"),
    ]
    again = tasks.file_debt(hive, rows)
    assert [(r["id"], r["created"]) for r in again] == [
        ("T-001", False), ("T-002", False), ("T-003", False),
    ]
    assert sorted(tasks.read_all_tasks(hive)) == ["T-001", "T-002", "T-003"]


def test_file_debt_dry_run_writes_nothing_and_names_what_exists(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    rows = debt.index_rows(_INDEX)
    tasks.file_debt(hive, rows[:1])
    out = tasks.file_debt(hive, rows, dry_run=True)
    assert [(r["debt_id"], r["id"], r["created"]) for r in out] == [
        ("T-1-D1", "T-001", False), ("T-1-D2", None, True), ("T-2-D2", None, True),
    ]
    assert sorted(tasks.read_all_tasks(hive)) == ["T-001"]


def test_a_resolved_entry_already_filed_is_left_alone(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    [filed] = tasks.file_debt(hive, [_row()])
    assert tasks.file_debt(hive, [_row(resolved=True)]) == []
    assert _read(hive, filed["id"]).status == "proposed"
