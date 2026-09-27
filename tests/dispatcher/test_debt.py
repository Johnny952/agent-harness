import re
import subprocess
from pathlib import Path

from dispatcher import debt, project_docs


def _declared(**fields):
    """One handoff's `debt` array, as the implementador returns it."""
    return {"debt": [fields]}


def _ruled(text, ruling):
    """One handoff's `debt_rulings` array, as the revisor returns it."""
    return {"debt_rulings": [{"debt": text, "ruling": ruling}]}


# --- the fingerprint two tellings of the same debt are matched by ---------


def test_fingerprint_survives_the_line_number_moving() -> None:
    """The revisor restates the declaration it is ruling on, and the index row
    was written by an earlier task: neither is ever byte-identical."""
    assert debt.fingerprint("Retry loop at cli.py:120 is untested") == debt.fingerprint(
        "retry loop at cli.py:214 is untested"
    )


def test_fingerprint_survives_the_punctuation_and_the_case() -> None:
    assert debt.fingerprint("The retry loop — untested!") == debt.fingerprint(
        "the retry loop, untested"
    )


def test_two_different_debts_do_not_share_a_fingerprint() -> None:
    assert debt.fingerprint("the retry loop is untested") != debt.fingerprint(
        "the config loader is untested"
    )


# --- what the implementador declared -------------------------------------


def test_declarations_reads_all_six_fields() -> None:
    payload = _declared(
        origin=debt.FOUND,
        what="The retry loop is untested",
        where="a task touching dispatcher/cli.py",
        why="the fixture needs a fake clock this task does not have",
        cost="a regression in the backoff lands silently",
        fix="a fake clock in conftest, then three cases",
    )

    [declaration] = debt.declarations(payload)

    assert declaration == debt.Declaration(
        what="The retry loop is untested",
        where="a task touching dispatcher/cli.py",
        why="the fixture needs a fake clock this task does not have",
        cost="a regression in the backoff lands silently",
        fix="a fake clock in conftest, then three cases",
        origin=debt.FOUND,
    )


def test_a_declaration_that_says_nothing_is_not_a_row() -> None:
    """The other five fields describe something; with no something there is
    nothing for a later task to match its own work against."""
    assert debt.declarations(_declared(what="   ", why="no time")) == []


def test_a_declaration_defaults_to_debt_this_task_created() -> None:
    """The stricter of the two: found debt is only declarable under rules the
    prompt spells out, so an origin nobody set should not claim to be found."""
    [declaration] = debt.declarations(_declared(what="The retry loop is untested"))

    assert declaration.origin == debt.INTRODUCED


def test_no_handoff_declares_no_debt() -> None:
    assert debt.declarations(None) == []
    assert debt.declarations({}) == []


# --- what the revisor ruled ----------------------------------------------


def test_a_ruling_is_found_through_the_revisors_own_wording() -> None:
    ruled = debt.rulings(_ruled("Retry loop at cli.py:214 — no test", debt.REJECTED))

    assert ruled[debt.fingerprint("Retry loop at cli.py:120, no test")] == debt.REJECTED


def test_a_word_that_is_not_a_ruling_is_not_one() -> None:
    assert debt.rulings(_ruled("the retry loop", "maybe")) == {}


def test_rejected_and_blocking_quote_the_revisor() -> None:
    payload = {
        "debt_rulings": [
            {"debt": "the retry loop is untested", "ruling": debt.REJECTED},
            {"debt": "the schema needs a column", "ruling": debt.BLOCKS},
            {"debt": "the log line is noisy", "ruling": debt.ACCEPTED},
        ]
    }

    assert debt.rejected(payload) == ["the retry loop is untested"]
    assert debt.blocking(payload) == ["the schema needs a column"]


def test_debt_the_revisor_never_mentioned_is_accepted() -> None:
    """Accept-by-default: a fingerprint miss costs one extra card, which a
    human notices, while the other default drops real debt invisibly."""
    accepted = debt.accepted(_declared(what="The retry loop is untested"), {"verdict": "APPROVED"})

    assert [declaration.what for declaration in accepted] == ["The retry loop is untested"]


def test_debt_the_revisor_rejected_is_not_filed() -> None:
    accepted = debt.accepted(
        _declared(what="The retry loop is untested"),
        _ruled("The retry loop is untested.", debt.REJECTED),
    )

    assert accepted == []


# --- the entry and the card that point at each other ----------------------


def test_entry_ids_are_numbered_inside_the_task_that_declared_them() -> None:
    """Derived, not assigned by the index: the dispatcher has to name them in
    the auditor's prompt before the auditor writes a single row."""
    assert debt.entry_id("task-7", 1) == "task-7-D1"
    assert debt.entry_id("task-7", 2) == "task-7-D2"


def test_a_card_title_carries_the_label_a_board_filter_looks_for() -> None:
    title = debt.card_title(debt.Declaration(what="The retry loop is untested"))

    assert title == "[debt] The retry loop is untested"


def test_a_long_card_title_is_cut_where_a_board_column_ends() -> None:
    title = debt.card_title(debt.Declaration(what="T" * 400))

    assert len(title) == debt.TITLE_MAX
    assert title.endswith("…")


def test_a_card_says_what_it_mirrors_and_what_moving_it_means() -> None:
    body = debt.card_description(
        "task-7",
        "task-7-D1",
        debt.Declaration(
            what="The retry loop is untested",
            where="a task touching dispatcher/cli.py",
            fix="a fake clock in conftest",
            origin=debt.FOUND,
        ),
    )

    assert "task-7" in body and "task-7-D1" in body
    assert project_docs.DEBT_INDEX in body
    assert debt.FOUND in body
    assert "a fake clock in conftest" in body
    # The one thing a human has to know before they touch it: this is a
    # record until they drag it out of the backlog.
    assert "backlog" in body


def test_a_card_leaves_out_the_fields_the_declaration_left_empty() -> None:
    body = debt.card_description("task-7", "task-7-D1", debt.Declaration(what="Untested"))

    assert "**Why it stays:**" not in body


# --- reading the index back ----------------------------------------------


_INDEX = """# Debt

| id | what | where | fix | card |
|---|---|---|---|---|
| `task-3-D1` | The retry loop is untested | a task touching `cli.py` | a fake clock | `card-aaa` |
| `task-4-D1` | The config loader guesses | a task adding a config key | read the schema | - |
"""


def _fake_cat(text, returncode=0):
    def fake_run_docker_exec(container, workdir, command, **kwargs):
        assert command == ["cat", project_docs.DEBT_INDEX]
        return subprocess.CompletedProcess(command, returncode, stdout=text, stderr="")

    return fake_run_docker_exec


def test_the_index_is_read_out_of_the_worktree_it_is_given(monkeypatch) -> None:
    """The branch being built, not the project checkout: a re-run has to see
    the entries its own earlier round already filed."""
    seen = []

    def fake_run_docker_exec(container, workdir, command, **kwargs):
        seen.append((container, workdir))
        return subprocess.CompletedProcess(command, 0, stdout=_INDEX, stderr="")

    monkeypatch.setattr(debt.docker_exec, "run_docker_exec", fake_run_docker_exec)

    text = debt.read_index("agent-cuenta1", "/data/projects/myproj/worktrees/task-1/work")

    assert text == _INDEX
    assert seen == [("agent-cuenta1", "/data/projects/myproj/worktrees/task-1/work")]


def test_a_project_with_no_debt_index_reads_as_empty(monkeypatch) -> None:
    monkeypatch.setattr(debt.docker_exec, "run_docker_exec", _fake_cat("", returncode=1))

    assert debt.read_index("agent-cuenta1", "/data/projects/myproj") == ""


def test_index_rows_reads_every_row_by_column_name() -> None:
    """The path-based way in, for a reader that already has the text: the read
    API mounts a checkout and cannot go out through `docker exec`."""
    rows = debt.index_rows(_INDEX)

    assert rows == [
        {
            "id": "task-3-D1",
            "what": "The retry loop is untested",
            # `_rows` unwraps one *matched* pair of backticks, which is what
            # turns `` `task-3-D1` `` into an id and what leaves this cell's
            # closing backtick alone (`docs/debt/T-009-D3.md`, fixed by T-010).
            # Before the fix the `where` cell read `a task touching `cli.py`
            # with its last character eaten.
            "where": "a task touching `cli.py`",
            "fix": "a fake clock",
            "card": "card-aaa",
            "resolved": False,
        },
        {
            "id": "task-4-D1",
            "what": "The config loader guesses",
            "where": "a task adding a config key",
            "fix": "read the schema",
            "card": "-",
            "resolved": False,
        },
    ]


def test_a_cell_keeps_the_backtick_it_only_opens_or_only_closes_with() -> None:
    """T-009-D3: one matched pair comes off a cell and nothing else.

    The three cells the old `cell.strip("`")` got wrong, in one table. A cell
    ending in inline code kept its opening backtick and lost its closing one; a
    cell wrapped in double backticks lost both of the inner pair as well; and a
    cell that opens with inline code lost the opening one.
    """
    text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| ``task-3-D1`` | `cli.py` is the one that opens with code | "
        "a task touching `cli.py` | wrap it in `try` | `card-aaa` |\n"
    )

    [row] = debt.index_rows(text)

    assert row["id"] == "`task-3-D1`"
    assert row["what"] == "`cli.py` is the one that opens with code"
    assert row["where"] == "a task touching `cli.py`"
    assert row["fix"] == "wrap it in `try`"
    assert row["card"] == "card-aaa"


def test_the_fingerprints_of_the_projects_own_index_did_not_move(monkeypatch) -> None:
    """The check T-009-D3's fix had to pass, run against the real index.

    Every row already filed has to fingerprint to what it fingerprinted before,
    or the next auditor re-files the whole backlog it inherited. Asserted by
    parsing `docs/debt/README.md` twice — once with the matched-pair unwrap and
    once with the one-backtick-per-end strip it replaced — rather than by
    restating the fingerprints as literals, which would pin today's rows instead
    of the property. It holds because `fingerprint` drops punctuation and then
    strips, so a backtick at either end of a **what** cell never reached it.
    """
    index = Path(__file__).parent.parent.parent / "docs" / "debt" / "README.md"
    text = index.read_text()

    rows = debt.index_rows(text)
    assert rows, "expected the project's own debt index to hold rows"
    assert all(re.fullmatch(r"T-\d+-D\d+", row["id"]) for row in rows), [
        row["id"] for row in rows
    ]
    after = debt.index_fingerprints(text)

    monkeypatch.setattr(debt, "_unwrap_code", lambda cell: cell.strip("`"))
    before = debt.index_fingerprints(text)
    ids_before = [row["id"] for row in debt.index_rows(text)]

    assert after == before
    assert [row["id"] for row in rows] == ids_before


def test_index_rows_falls_back_to_the_column_order_like_its_two_readers() -> None:
    renamed = (
        "| entry | debt | trigger | remedy | issue |\n"
        "|---|---|---|---|---|\n"
        "| `task-3-D1` | The retry loop is untested | anywhere | a fake clock | `card-aaa` |\n"
    )

    [row] = debt.index_rows(renamed)

    assert row["id"] == "task-3-D1"
    assert row["where"] == "anywhere"
    assert row["card"] == "card-aaa"


def test_index_rows_offers_the_resolved_flag_without_hiding_the_row() -> None:
    """The index marks an entry resolved in place, by opening its **what**
    cell with a bolded `Resolved`. That is a convention in prose, so the flag
    is best-effort and the row is never dropped on the strength of it."""
    text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-3-D1` | **Resolved 2026-09-26.** The retry loop was untested | x | y | none |\n"
        "| `task-4-D1` | **Not** resolved at all | x | y | none |\n"
    )

    rows = debt.index_rows(text)

    assert [row["resolved"] for row in rows] == [True, False]
    assert [row["id"] for row in rows] == ["task-3-D1", "task-4-D1"]


def test_index_rows_of_no_index_is_no_rows() -> None:
    assert debt.index_rows("") == []
    assert debt.index_rows("# Debt\n\nNothing filed yet.\n") == []


def test_index_rows_of_a_short_row_reads_the_cells_that_are_there() -> None:
    """The auditor writes the table by hand; a row missing its last cell is a
    row with no card, not a read that throws the index away."""
    text = "| id | what | where | fix | card |\n|---|---|---|---|---|\n| `task-3-D1` | Untested |\n"

    [row] = debt.index_rows(text)

    assert row["what"] == "Untested"
    assert row["where"] == ""


def test_the_index_says_what_it_already_carries() -> None:
    assert debt.index_fingerprints(_INDEX) == {
        debt.fingerprint("The retry loop is untested"),
        debt.fingerprint("The config loader guesses"),
    }


def test_a_renamed_heading_costs_a_dedupe_not_a_crash() -> None:
    """The auditor writes the table by hand. Falling back to the column order
    keeps a re-worded heading from throwing the whole read away."""
    renamed = (
        "| entry | debt | trigger | remedy | issue |\n"
        "|---|---|---|---|---|\n"
        "| `task-3-D1` | The retry loop is untested | anywhere | a fake clock | `card-aaa` |\n"
    )

    assert debt.index_fingerprints(renamed) == {debt.fingerprint("The retry loop is untested")}
    assert debt.card_ids(renamed, ["task-3-D1"]) == {"task-3-D1": "card-aaa"}


def test_prose_between_two_tables_does_not_carry_the_header_over() -> None:
    text = _INDEX + "\nSome prose.\n\n| id | what |\n|---|---|\n| `task-5-D1` | Something else |\n"

    assert debt.fingerprint("Something else") in debt.index_fingerprints(text)


def test_the_cards_to_close_come_from_the_rows_that_have_one() -> None:
    assert debt.card_ids(_INDEX, ["task-3-D1", "task-4-D1", "task-9-D1"]) == {
        "task-3-D1": "card-aaa"
    }


def test_the_entries_a_task_says_it_resolved_are_read_once_each() -> None:
    payload = {"resolved_debt": ["`task-3-D1`", "task-3-D1", "task-4-D1", "  "]}

    assert debt.resolved(payload) == ["task-3-D1", "task-4-D1"]


# --- what the auditor is told about them ---------------------------------


def test_a_task_that_filed_nothing_says_nothing_to_the_auditor() -> None:
    assert debt.filing_note([]) == ""


def test_the_note_hands_the_auditor_both_ids_and_the_column_order() -> None:
    note = debt.filing_note(
        [("task-7-D1", "card-aaa", debt.Declaration(what="The retry loop is untested"))]
    )

    assert " | ".join(debt.COLUMNS) in note
    assert "`task-7-D1`" in note and "`card-aaa`" in note
    assert "The retry loop is untested" in note
    assert project_docs.DEBT_INDEX in note
    # The closing rule, because an entry with no id here has nothing to point at.
    assert "Do not file anything else as debt" in note


def test_the_note_says_so_when_the_project_has_no_board() -> None:
    """The index is the source of truth, so a board-less project still files
    the row — with nothing in the column that mirrors a card."""
    note = debt.filing_note([("task-7-D1", None, debt.Declaration(what="Untested"))])

    assert "no card" in note
