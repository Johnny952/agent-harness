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


def test_the_filing_note_keeps_the_origin_out_of_the_row() -> None:
    note = debt.filing_note([("T-1-D1", debt.Declaration(what="w", origin=debt.FOUND))])
    assert "`what` cell" in note and "`**Origin:**`" in note and "never in the row" in note


def test_a_declared_row_keeps_its_what_verbatim_whatever_its_origin() -> None:
    row = debt.declared_row("T-1-D1", debt.Declaration(what="No probe (TTL).", origin=debt.FOUND))
    assert row["what"] == "No probe (TTL)."


@pytest.mark.parametrize("word", ["Introduced", "Found"])
def test_backlog_item_drops_a_trailing_origin_word(word) -> None:
    title, description, _ = debt.backlog_item(_row(what=f"No probe: held (TTL). {word}"))
    assert title == "T-1-D1: No probe: held (TTL)."
    assert description.startswith("## Goal\n\nNo probe: held (TTL).\n\n")


@pytest.mark.parametrize("what", ["Found by the operator while x", "Nothing was found"])
def test_backlog_item_leaves_other_uses_of_the_word(what) -> None:
    title, _, _ = debt.backlog_item(_row(what=what))
    assert title == f"T-1-D1: {what}"


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
    tasks.accept(hive, first["id"], "dropped", reason="not needed", by="operator")
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


# --- blocked_by (ADR 396) ------------------------------------------------------


@pytest.mark.parametrize(
    "value, kind",
    [("T-163", "task"), ("T-164-S2", "task"), ("task-1", "task"), ("`T-163`", "task"),
     ("docs/plans/x.md step 2", "step"), ("T-163 and more", "step"), ("", "")],
)
def test_blocker_kind(value, kind) -> None:
    assert debt.blocker_kind(value) == kind


@pytest.mark.parametrize(
    "where, value",
    [("Blocked by T-163; cli.py", "T-163"),
     ("**Blocked by** T-163; cli.py", "T-163"),
     ("blocked by: `docs/plans/x.md` step 2; later", "`docs/plans/x.md` step 2"),
     ("Blocked by `T-163`", "T-163"),
     ("cli.py", ""),
     ("a task that is blocked by T-12 midway", "")],
)
def test_blocked_by_reads_the_lead_of_the_where(where, value) -> None:
    assert debt.blocked_by({"where": where}) == value


def test_the_key_of_a_row_wins_over_its_where() -> None:
    assert debt.blocked_by({"where": "Blocked by T-1; x", "blocked_by": ""}) == ""
    assert debt.blocked_by({"where": "x", "blocked_by": "T-2"}) == "T-2"


def test_declarations_read_blocked_by_and_the_note_tells_the_auditor() -> None:
    [free, task, step] = debt.declarations({"debt": [
        {"what": "a"}, {"what": "b", "blocked_by": "T-9"},
        {"what": "c", "blocked_by": "docs/plans/x.md step 2"},
    ]})
    assert (free.blocked_by, task.blocked_by) == ("", "T-9")
    note = debt.filing_note([("t-D1", free), ("t-D2", task), ("t-D3", step)])
    assert "- `t-D1` — a\n" in note
    assert '- `t-D2` — b — blocked by T-9: open its where with "Blocked by T-9;"\n' in note
    assert "not filed as a backlog item, the row is the record" in note.split("t-D3")[1]
    assert "not filed as a backlog item" not in note.split("t-D3")[0]


def test_file_debt_follows_blocked_by_in_all_three_cases(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-050")
    rows = [
        _row("T-1-D1"),
        _row("T-1-D2", where="Blocked by T-050; cli.py"),
        _row("T-1-D3", where="Blocked by docs/plans/x.md step 2; cli.py"),
        _row("T-1-D4", blocked_by="T-999"),
    ]
    out = tasks.file_debt(hive, rows)
    assert [(r["debt_id"], r["gated"], r["depends_on"], r["created"]) for r in out] == [
        ("T-1-D1", False, [], True), ("T-1-D2", False, ["T-050"], True),
        ("T-1-D3", True, [], False), ("T-1-D4", False, [], True),
    ]
    assert out[2]["id"] is None and out[2]["blocked_by"] == "docs/plans/x.md step 2"
    assert [r["unknown_blocker"] for r in out] == [False, False, False, True]
    items = {t.debt_id: t.depends_on for t in tasks.read_all_tasks(hive).values() if t.debt_id}
    assert items == {"T-1-D1": [], "T-1-D2": ["T-050"], "T-1-D4": []}


def test_file_debt_dry_run_follows_blocked_by_and_a_gated_row_names_an_existing_item(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-050")
    tasks.file_debt(hive, [_row("T-1-D3")])
    rows = [_row("T-1-D2", blocked_by="T-050"), _row("T-1-D3", blocked_by="docs/x.md step 1")]
    out = tasks.file_debt(hive, rows, dry_run=True)
    assert [(r["id"], r["created"], r["gated"], r["depends_on"]) for r in out] == [
        (None, True, False, ["T-050"]), ("T-051", False, True, []),
    ]
    assert not [t for t in tasks.read_all_tasks(hive).values() if t.debt_id == "T-1-D2"]
    assert [r["unknown_blocker"] for r in out] == [False, False]
    [lost] = tasks.file_debt(hive, [_row("T-1-D4", blocked_by="T-999")], dry_run=True)
    assert lost["unknown_blocker"] is True


def test_file_debt_looks_an_entry_up_once_on_a_real_row(tmp_path: Path, monkeypatch) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-050")
    calls = []
    real = tasks.find_debt_item
    monkeypatch.setattr(
        tasks, "find_debt_item", lambda h, d: calls.append(d) or real(h, d)
    )
    tasks.file_debt(hive, [_row("T-1-D1"), _row("T-1-D2", blocked_by="T-050")])
    assert calls == ["T-1-D1", "T-1-D2"]
    calls.clear()
    tasks.file_debt(hive, [_row("T-1-D3")], dry_run=True)
    tasks.file_debt(hive, [_row("T-1-D4", blocked_by="docs/x.md step 1")])
    assert calls == ["T-1-D3", "T-1-D4"]


def test_propose_debt_depends_on_is_written_and_an_unreadable_dep_is_refused(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    _task(hive, "T-050")
    made = tasks.propose_debt(
        hive, debt_id="T-1-D1", title="t", description=_DESCRIPTION, priority="high",
        depends_on=["T-050"],
    )
    assert _read(hive, made["id"]).depends_on == ["T-050"]
    with pytest.raises(tasks.BoardWriteError):
        tasks.propose_debt(
            hive, debt_id="T-1-D2", title="t2", description=_DESCRIPTION, priority="high",
            depends_on=["T-404"],
        )


def test_a_resolved_entry_already_filed_is_left_alone(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    [filed] = tasks.file_debt(hive, [_row()])
    assert tasks.file_debt(hive, [_row(resolved=True)]) == []
    assert _read(hive, filed["id"]).status == "proposed"
