# dispatcher/debt.py
"""Work a task decided not to do, declared, ruled on, and filed in the index.

Debt that only exists as a line in a handoff is debt nobody will ever find
again: the handoff is read by the next phase of *this* task and by nobody
else. So it takes a fixed route. The implementador declares it, the revisor
rules on each declaration, and the auditor — the only writer of the indexes —
files the accepted ones in `docs/debt/`.

Two rules shape everything here:

- **The index is the only record.** It is versioned with the code, so a clone
  has it. Nothing mirrors a row anywhere else (`docs/decisions.md` ADR 59);
  the index keeps its `card` column because the api and the console read it,
  and the auditor writes `none` there. The link the other way is the backlog
  item's `debt_id` (ADR 131): the dispatcher files each new open entry as a
  `proposed` item naming it, so the index never has to be rewritten to point
  at the board.
- **The dispatcher assigns the ids, not an agent.** An agent re-run for a
  second review round would number the same entry twice.

This module is the pieces of that flow that are not prompt text: the shape of
a declaration, the fingerprint two of them are compared by, who ruled what,
and how an index row is read back.
"""
from __future__ import annotations

import dataclasses
import re

from dispatcher import docker_exec, project_docs

#: Where a declaration came from. `found` debt counts only in files the task
#: touched and is not already in the index — otherwise every task would
#: re-declare the whole backlog it inherited.
INTRODUCED = "introduced"
FOUND = "found"
ORIGINS = (INTRODUCED, FOUND)

#: What the revisor can do with one declaration. `blocks` is the escape hatch
#: for a block wearing a debt costume: a decision the task does not specify, a
#: schema change, anything that is not a choice to leave work undone.
ACCEPTED = "accepted"
REJECTED = "rejected"
BLOCKS = "blocks"
#: `follow_up` is not debt but the next task (ADR 393): it is filed as a backlog
#: item that depends on the declaring task, with no id and no debt file.
FOLLOW_UP = "follow_up"
RULINGS = (ACCEPTED, REJECTED, BLOCKS, FOLLOW_UP)

#: The index's columns, in order. The dispatcher reads **what** back to dedupe
#: and the api serves them by name, so this is a contract, not a suggestion,
#: and it is handed to the auditor as one. `impact` is last (ADR 131): a row
#: written before it has five cells, and its `card` stays the fifth.
COLUMNS = ("id", "what", "where", "fix", "card", "impact")

#: What an entry costs if left, as the auditor judges it (ADR 131): `risk`, it
#: can break or lose something; `cost`, it slows or costs work; `cosmetic`,
#: wording or polish. A row with none of these, a row written before the
#: column among them, reads as `NO_IMPACT`.
IMPACTS = ("risk", "cost", "cosmetic")
NO_IMPACT = "none"

#: The backlog priority an entry's item is filed at. `NO_IMPACT` is the
#: board's default bucket, the same as `cost`: an unjudged entry is neither
#: raised nor buried.
IMPACT_PRIORITY = {"risk": "high", "cost": "normal", "cosmetic": "low"}
DEFAULT_IMPACT_PRIORITY = "normal"

_VOLATILE_RE = re.compile(r"[0-9a-f]{6,}|\d+")
_NOISE_RE = re.compile(r"[^a-z0-9]+")


def fingerprint(text: str) -> str:
    """What two statements of the same debt have in common.

    The same technique the learnings inbox dedupes by: lowercase, flatten the
    parts that move between two tellings of the same thing (line numbers,
    hashes, counts), and drop the punctuation. It is used for two comparisons
    that are never exact-match — the revisor restating a declaration it is
    ruling on, and an index row written by an earlier task — and both of them
    fail safe: an unrecognised fingerprint means the debt is accepted and
    filed, never that it is silently dropped.
    """
    flattened = _VOLATILE_RE.sub("0", (text or "").lower())
    return _NOISE_RE.sub(" ", flattened).strip()[:200]


@dataclasses.dataclass(frozen=True)
class Declaration:
    """One piece of work a task chose not to do, as the implementador states it."""

    what: str
    where: str = ""
    why: str = ""
    cost: str = ""
    fix: str = ""
    origin: str = INTRODUCED
    blocked_by: str = ""

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.what)


def _text(item: dict, key: str) -> str:
    return str(item.get(key, "") or "").strip()


def declarations(payload: dict | None) -> list[Declaration]:
    """The debt one handoff declares, in the order it declared it.

    A declaration with nothing in `what` is dropped rather than filed as an
    empty row: the rest of the fields describe something, and without the
    something there is nothing for a later task to match against.
    """
    found = []
    for item in (payload or {}).get("debt") or []:
        if not isinstance(item, dict):
            continue
        what = _text(item, "what")
        if not what:
            continue
        found.append(
            Declaration(
                what=what,
                where=_text(item, "where"),
                why=_text(item, "why"),
                cost=_text(item, "cost"),
                fix=_text(item, "fix"),
                origin=_text(item, "origin") or INTRODUCED,
                blocked_by=_text(item, "blocked_by"),
            )
        )
    return found


def rulings(payload: dict | None) -> dict[str, str]:
    """Fingerprint to ruling, as the revisor handed them down.

    Keyed by fingerprint because the revisor restates the declaration in its
    own return and a restatement is never byte-identical.
    """
    ruled = {}
    for item in (payload or {}).get("debt_rulings") or []:
        if not isinstance(item, dict):
            continue
        debt = _text(item, "debt")
        ruling = _text(item, "ruling").lower()
        if debt and ruling in RULINGS:
            ruled[fingerprint(debt)] = ruling
    return ruled


def _ruled(payload: dict | None, wanted: str) -> list[str]:
    return [
        _text(item, "debt")
        for item in (payload or {}).get("debt_rulings") or []
        if isinstance(item, dict) and _text(item, "ruling").lower() == wanted
    ]


def rejected(payload: dict | None) -> list[str]:
    """The declarations this review sent back, as the revisor stated them.

    Non-empty means the round is not an approval, whatever the verdict field
    says: a rejected declaration is a finding like any other, and a finding is
    fixed in the next round.
    """
    return [text for text in _ruled(payload, REJECTED) if text]


def blocking(payload: dict | None) -> list[str]:
    """The declarations that were really blocks, as the revisor stated them.

    Non-empty ends the task blocked, without another round: another round
    cannot supply a decision the task was never given.
    """
    return [text for text in _ruled(payload, BLOCKS) if text]


def accepted(implemented: dict | None, reviewed: dict | None) -> list[Declaration]:
    """The debt that gets filed: everything declared and not ruled against.

    Accept-by-default, deliberately. This is only ever called on the round the
    revisor approved, and an approving round has rejected nothing — so the
    open question is not "was this accepted?" but "did the fingerprint match?"
    A miss then costs one extra row in the index, while the other default
    would silently drop real debt on a wording change. One of those a human
    notices; the other is invisible.
    """
    ruled = rulings(reviewed)
    return [
        declaration
        for declaration in declarations(implemented)
        if ruled.get(declaration.fingerprint, ACCEPTED) == ACCEPTED
    ]


def follow_ups(implemented: dict | None, reviewed: dict | None) -> list[Declaration]:
    """The declarations the revisor ruled `follow_up`, in declared order."""
    ruled = rulings(reviewed)
    return [
        declaration
        for declaration in declarations(implemented)
        if ruled.get(declaration.fingerprint) == FOLLOW_UP
    ]


def entry_id(task_id: str, number: int) -> str:
    """The id of the nth entry a task files, stable across a re-run.

    Derived rather than assigned by the index, so the dispatcher can name the
    entries in the auditor's prompt *before* the auditor writes them.
    """
    return f"{task_id}-D{number}"


def read_index(container: str, workdir: str) -> str:
    """The project's debt index as it stands, or empty when there is none.

    Read from the worktree the task is being built in rather than from the
    project checkout, so a task that already filed entries on its own branch —
    a re-run, a second cycle — sees them and does not file them twice.
    """
    proc = docker_exec.run_docker_exec(container, workdir, ["cat", project_docs.DEBT_INDEX])
    return proc.stdout if proc.returncode == 0 else ""


def _is_code_span(cell: str) -> bool:
    """Whether the cell is one code span and nothing else.

    What a cell has to be before `_unwrap_code` takes a pair off it: the outer
    backticks have to be *its* pair, not the opening of one span and the
    closing of another. A **where** cell that names two files
    (`` `debt.py` or `cli.py` ``) starts and ends with a backtick and is still
    two spans, and unwrapping it moved both delimiters inwards, which
    `/api/debt` and the console's debt screen render as plain text
    (`docs/debt/T-010-D2.md`, fixed here). Nesting is the one thing an interior
    backtick may legitimately be — `` `x` `` wrapped again is one span — which
    is why this recurses rather than rejecting any backtick it sees inside.
    """
    if len(cell) < 2 or not cell.startswith("`") or not cell.endswith("`"):
        return False
    inner = cell[1:-1]
    return "`" not in inner or _is_code_span(inner)


def _unwrap_code(cell: str) -> str:
    """One matched pair of backticks off a cell, and never half of one.

    The index writes an id and a card in backticks — `` `T-008-D1` `` — and the
    readers below want the id. `str.strip("`")` used to do it, which also ate a
    lone backtick at either end: a **where** cell that ends in inline code
    (``a task touching `cli.py` ``) lost its closing one, and `/api/debt`
    rendering those cells to a human is what made it visible
    (`docs/debt/T-009-D3.md`, fixed by T-010). A pair or nothing, so a cell
    whose text legitimately opens or closes with a backtick survives intact.
    No fingerprint moves: the **what** cell of an existing row is wrapped in no
    backticks at all, so it unwrapped to itself before this and does now.
    Whether the pair is one is `_is_code_span`'s question, not this one's.
    """
    if _is_code_span(cell):
        return cell[1:-1]
    return cell


def _rows(text: str) -> list[tuple[list[str], list[str]]]:
    """Every data row of every Markdown table in the text, with its header."""
    header: list[str] = []
    rows = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = []  # a new table below gets its own header
            continue
        cells = [_unwrap_code(cell.strip()).strip() for cell in stripped.strip("|").split("|")]
        if not header:
            header = [cell.lower() for cell in cells]
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the ---|--- rule under the header
        rows.append((cells, header))
    return rows


def _column(cells: list[str], header: list[str], name: str, fallback: int) -> str:
    """One cell, by column name, falling back to its position.

    The auditor is told the column order, but it writes the table by hand and
    a renamed heading should cost a dedupe, not a crash.
    """
    index = header.index(name) if name in header else fallback
    try:
        return cells[index]
    except IndexError:
        return ""


#: How the index marks an entry resolved: the **what** cell opens with a bolded
#: `Resolved`, per that index's own rule. A convention in prose and not a
#: schema, which is why the flag it produces is offered and never used to hide
#: a row.
_RESOLVED_RE = re.compile(r"^\*\*\s*resolved\b", re.IGNORECASE)


def impact_of(cell: str) -> str:
    """An impact cell read as one of `IMPACTS`, or `NO_IMPACT`.

    Case and surrounding blanks and bold do not count; anything else, a typo
    included, is no impact rather than a guess at one.
    """
    value = (cell or "").strip().strip("*").strip().lower()
    return value if value in IMPACTS else NO_IMPACT


def priority_for(impact: str) -> str:
    """The backlog priority an entry with this impact is filed at (ADR 131)."""
    return IMPACT_PRIORITY.get(impact, DEFAULT_IMPACT_PRIORITY)


def _impact_cell(cells: list[str], header: list[str]) -> str:
    # By name only: a five-column row has no impact, and its last cell is the
    # card, which position would read as one.
    if "impact" not in header:
        return ""
    return _column(cells, header, "impact", len(cells))


def _raw_rows(text: str) -> list[dict]:
    """Every row as written, one dict per table line, nothing folded."""
    return [
        {
            "id": _column(cells, header, "id", 0),
            "what": (what := _column(cells, header, "what", 1)),
            "where": _column(cells, header, "where", 2),
            "fix": _column(cells, header, "fix", 3),
            "card": _column(cells, header, "card", 4),
            "impact": impact_of(_impact_cell(cells, header)),
            "resolved": bool(_RESOLVED_RE.match(what)),
        }
        for cells, header in _rows(text)
    ]


def _is_resolution(row: dict) -> bool:
    """A row appended to resolve an entry (ADR 297): resolved, with no **where**.

    A row resolved in place keeps the entry's **where**, so it is never taken
    for one.
    """
    return bool(row["id"]) and row["resolved"] and not row["where"]


def _fold(entry: dict, resolution: dict) -> dict:
    """The entry with its resolution row folded in: the shape a row resolved in
    place always had, so no reader has to know there were two rows."""
    fix = " ".join(part for part in (entry["fix"], resolution["fix"]) if part)
    return {
        **entry,
        "what": f"{resolution['what']} {entry['what']}".strip(),
        "fix": fix,
        "impact": NO_IMPACT,
        "resolved": True,
    }


def index_rows(text: str) -> list[dict]:
    """Every row of the index, by column name, in the order it is written.

    The path-based way in. `read_index` reaches a project's index through
    `docker exec` and the row parsing behind it was private, so anything that
    already has the text — Phase 1's read API, reading a mounted checkout —
    had no way to it but a second parser. This is that way, and the two readers
    below go through it so there is still only one.

    A resolution row (ADR 297) is folded into the first row with its id and
    does not appear on its own. A later row with the same id that is not a
    resolution, and a resolution with no entry before it, are returned as
    written: nothing is lost.

    `resolved` is best-effort by construction: see `_RESOLVED_RE`.
    """
    rows: list[dict] = []
    first: dict[str, int] = {}
    for row in _raw_rows(text):
        position = first.get(row["id"]) if row["id"] else None
        if position is not None and _is_resolution(row):
            rows[position] = _fold(rows[position], row)
            continue
        if row["id"] and position is None:
            first[row["id"]] = len(rows)
        rows.append(row)
    return rows


def duplicate_ids(text: str) -> list[str]:
    """The ids with more than one row that is not a resolution row.

    What a union merge of two in-place edits of one row leaves behind, and
    what `gates.debt_index` stops (ADR 297). In the order of the first repeat.
    """
    seen: set[str] = set()
    repeated: list[str] = []
    for row in _raw_rows(text):
        if not row["id"] or _is_resolution(row):
            continue
        if row["id"] in seen and row["id"] not in repeated:
            repeated.append(row["id"])
        seen.add(row["id"])
    return repeated


def open_rows(text: str) -> list[dict]:
    """The index's open entries: rows with an id that are not marked resolved.

    What the backlog mirrors (ADR 131). A resolved row is never filed as an
    item; one already filed stays as it is, for the operator to drop.
    """
    return [row for row in index_rows(text) if row["id"] and not row["resolved"]]


#: The longest title a backlog item takes (`tasks.MAX_TITLE_CHARS`), repeated
#: so this module needs no import of the board's.
_MAX_TITLE_CHARS = 200
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


#: An entry's origin, spilled into the end of its what cell by an auditor
#: copying earlier rows (ADR 426); it is not part of the what.
_ORIGIN_TAIL_RE = re.compile(r"\s+(?:Introduced|Found)\s*\Z")


def _one_line(text: str) -> str:
    return " ".join(_BOLD_RE.sub(r"\1", text or "").split())


def backlog_item(row: dict) -> tuple[str, str, str]:
    """An index row as a backlog item: (title, description, priority).

    The title is the entry's id and its **what**, on one line and cut to fit;
    the id in it keeps two entries that say the same thing two titles. The
    description is the entry's **what** and **where** as its goal and its
    **fix** as what done means, with a pointer to the entry's own file, which
    says the rest. The priority comes from the row's impact (`priority_for`).
    """
    entry = row["id"]
    what = _ORIGIN_TAIL_RE.sub("", _one_line(row.get("what", ""))) or "No description in the index."
    title = f"{entry}: {what}"
    if len(title) > _MAX_TITLE_CHARS:
        title = title[: _MAX_TITLE_CHARS - 3].rstrip() + "..."
    goal = [what, ""]
    where = _one_line(row.get("where", ""))
    if where:
        goal += [f"Where: {where}", ""]
    goal.append(
        f"Filed from the debt index entry `{entry}`; its file is "
        f"`{project_docs.DEBT_DIR}/{entry}.md`."
    )
    fix = _one_line(row.get("fix", "")) or (
        f"The entry `{entry}` has a resolution row in `{project_docs.DEBT_INDEX}`."
    )
    description = "## Goal\n\n" + "\n".join(goal) + "\n\n## Done when\n\n" + fix + "\n"
    return title, description, priority_for(row.get("impact", NO_IMPACT))


def follow_up_item(task_id: str, declaration: Declaration) -> tuple[str, str]:
    """A `follow_up` declaration as a backlog item: (title, description).

    The title carries the declaring task's id, which keeps two tasks' identical
    follow-ups apart and is what `tasks.propose` is idempotent on (ADR 394).
    The description is built the way `backlog_item` builds one.
    """
    what = _one_line(declaration.what) or "No description."
    title = f"{task_id} follow-up: {what}"
    if len(title) > _MAX_TITLE_CHARS:
        title = title[: _MAX_TITLE_CHARS - 3].rstrip() + "..."
    goal = [what, ""]
    where = _one_line(declaration.where)
    if where:
        goal += [f"Where: {where}", ""]
    goal.append(f"Declared as a follow-up by `{task_id}`.")
    fix = _one_line(declaration.fix) or (
        f"The follow-up `{what}` declared by `{task_id}` is built."
    )
    description = "## Goal\n\n" + "\n".join(goal) + "\n\n## Done when\n\n" + fix + "\n"
    return title, description


def declared_row(entry: str, declaration: Declaration) -> dict:
    """A filed entry the index does not show yet, as a row from its declaration.

    Its impact is `NO_IMPACT`: only the auditor judges one.
    """
    return {
        "id": entry,
        "what": declaration.what,
        "where": declaration.where,
        "fix": declaration.fix,
        "card": "none",
        "impact": NO_IMPACT,
        "resolved": False,
        "blocked_by": declaration.blocked_by,
    }


#: A blocker that is a task id (`T-163`, `T-164-S2`); anything else is a plan step (ADR 396).
TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+(?:-S\d+)?$")
_BLOCKED_BY_RE = re.compile(
    r"^\s*(?:\*\*)?blocked by:?(?:\*\*)?\s+(?P<value>[^;]+?)\s*(?:\*\*)?\s*(?:;|$)",
    re.IGNORECASE,
)


def blocker_kind(value: str) -> str:
    """`task`, `step` or `` for what a `blocked_by` names (ADR 396, decision 2)."""
    value = _unwrap_code((value or "").strip()).strip()
    if not value:
        return ""
    return "task" if TASK_ID_RE.match(value) else "step"


def blocked_by(row: dict) -> str:
    """What gates a row: its `blocked_by` key, else the lead of its **where** (ADR 396, decision 3).

    A row from the index has no key, only the `Blocked by <value>;` the auditor
    opens the where cell with; a row built from a declaration carries the key,
    empty when nothing gates it. A row without either reads as not gated.
    """
    if "blocked_by" in row:
        return _unwrap_code(str(row["blocked_by"] or "").strip()).strip()
    found = _BLOCKED_BY_RE.match(str(row.get("where", "") or ""))
    if not found:
        return ""
    return _unwrap_code(found.group("value").strip()).strip()


def index_fingerprints(text: str) -> set[str]:
    """What the index already holds, for deciding what is new.

    Only accepted debt gets an id, and only debt the index does not already
    describe: an index that fills with the same entry once per task is an
    index nobody reads.
    """
    return {fingerprint(row["what"]) for row in index_rows(text) if row["what"]}


def resolved(payload: dict | None) -> list[str]:
    """The entry ids one handoff claims to have resolved.

    Unwrapped through `_unwrap_code`, because a handoff writes the id the way
    the index shows it and the two readers have to agree on what it says. The
    `str.strip("`")` this replaces ate half a pair here for as long after
    `_rows` stopped doing it as it took someone to notice
    (`docs/debt/T-010-D1.md`, fixed here).
    """
    seen = []
    for item in (payload or {}).get("resolved_debt") or []:
        entry = _unwrap_code(str(item or "").strip()).strip()
        if entry and entry not in seen:
            seen.append(entry)
    return seen


def filing_note(filed: list[tuple[str, Declaration]]) -> str:
    """What the auditor is told about the entries it is about to write.

    The ids are assigned by the dispatcher, before the phase runs, so a
    re-run names the same entry the same way; the auditor stays the only
    writer of the index.
    """
    if not filed:
        return ""
    columns = " | ".join(COLUMNS)
    lines = [
        "The debt the revisor accepted in this task already has its ids. Append one row per "
        f"entry at the end of the table in `{project_docs.DEBT_INDEX}`, with these columns in this order: {columns}. "
        "Use the id given here, and write `none` in the `card` column. Write each entry's what in "
        "the `what` cell as listed below and end it there: its origin (`introduced` or `found`) "
        "goes on the entry file's `**Origin:**` line, never in the row. In the `impact` "
        "column write one of `risk` (left as it is, it can break or lose something), "
        "`cost` (it slows or costs work) or `cosmetic` (wording or polish): the dispatcher "
        "files each entry as a backlog item and its impact sets the item's priority. "
        "Then write the entry itself as a file under "
        f"`{project_docs.DEBT_DIR}/`, named after its id.",
        "",
    ]
    for entry, declaration in filed:
        line = f"- `{entry}` — {declaration.what}"
        gate = _unwrap_code(declaration.blocked_by.strip()).strip()
        if gate:
            line += f" — blocked by {gate}: open its where with \"Blocked by {gate};\""
            if blocker_kind(gate) == "step":
                line += "; it is not filed as a backlog item, the row is the record"
        lines.append(line)
    lines.append("")
    lines.append("Resolve an entry by appending a resolution row, never by editing a row (ADR 297).")
    lines.append("")
    lines.append(
        "Do not file anything else as debt: work the revisor did not accept is not an entry, "
        "and neither is one with no id here."
    )
    return "\n".join(lines)
