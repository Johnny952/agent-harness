# dispatcher/debt.py
"""Work a task decided not to do, declared, ruled on, and put on the board.

Debt that only exists as a line in a handoff is debt nobody will ever find
again: the handoff is read by the next phase of *this* task and by nobody
else. So it takes a fixed route. The implementador declares it, the revisor
rules on each declaration, the auditor — the only writer of the indexes —
files the accepted ones in `docs/debt/`, and the dispatcher mirrors each
filed entry as one card on the board.

Two rules shape everything here:

- **The index is the source of truth, the board is a viewing aid.** The index
  is versioned with the code, so a clone has it and a project with no board
  loses nothing. That is why the dispatcher can run the whole flow against a
  `NullKanbanClient` and only the cards go missing.
- **The dispatcher creates the cards, not an agent.** An agent that can create
  tasks can assign itself work, and an agent re-run for a second review round
  would create the same card twice.

This module is the pieces of that flow that are not prompt text: the shape of
a declaration, the fingerprint two of them are compared by, who ruled what,
and how an index row and a card point at each other.
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
RULINGS = (ACCEPTED, REJECTED, BLOCKS)

#: The index's columns, in order. The dispatcher reads two of them back — the
#: entry id and the card it points at — so this is a contract, not a
#: suggestion, and it is handed to the auditor as one.
COLUMNS = ("id", "what", "where", "fix", "card")

#: The label the spec asks every debt card to carry. `create_issue` takes no
#: labels, so it rides in the title, where a board filter can still find it.
LABEL = "debt"

#: Same cap as a task card's title, for the same reason: a board column shows
#: the first line and a 400-character title is a wall.
TITLE_MAX = 120

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
    A miss then costs one extra card on the board, while the other default
    would silently drop real debt on a wording change. One of those a human
    notices; the other is invisible.
    """
    ruled = rulings(reviewed)
    return [
        declaration
        for declaration in declarations(implemented)
        if ruled.get(declaration.fingerprint, ACCEPTED) == ACCEPTED
    ]


def entry_id(task_id: str, number: int) -> str:
    """The id of the nth entry a task files, stable across a re-run.

    Derived rather than assigned by the index, so the dispatcher can name the
    entries in the auditor's prompt *before* the auditor writes them, which is
    what lets each side record the other's id in one pass.
    """
    return f"{task_id}-D{number}"


def card_title(declaration: Declaration) -> str:
    title = f"[{LABEL}] {declaration.what}".replace("\n", " ").strip()
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1].rstrip() + "…"


def card_description(task_id: str, entry: str, declaration: Declaration) -> str:
    """The card's body: the declaration, plus where the real entry lives.

    Deliberately a copy and not a summary — a human triaging the board should
    not have to clone the repo to know what they are approving — but the entry
    it points at stays the source of truth, and it is named here so the card
    can never become the only record of anything.
    """
    lines = [
        f"Declared by task `{task_id}` ({declaration.origin}).",
        "",
        f"**What:** {declaration.what}",
    ]
    for label, value in (
        ("Where", declaration.where),
        ("Why it stays", declaration.why),
        ("Cost of leaving it", declaration.cost),
        ("Fix", declaration.fix),
    ):
        if value:
            lines.append(f"**{label}:** {value}")
    lines += [
        "",
        f"Entry `{entry}` in `{project_docs.DEBT_INDEX}`, which is the source of truth; "
        "this card mirrors it.",
        "",
        "In the backlog this is a record, not work. Move it out of the backlog to "
        "approve the work; the task that resolves it closes this card.",
    ]
    return "\n".join(lines)


def read_index(container: str, workdir: str) -> str:
    """The project's debt index as it stands, or empty when there is none.

    Read from the worktree the task is being built in rather than from the
    project checkout, so a task that already filed entries on its own branch —
    a re-run, a second cycle — sees them and does not file them twice.
    """
    proc = docker_exec.run_docker_exec(container, workdir, ["cat", project_docs.DEBT_INDEX])
    return proc.stdout if proc.returncode == 0 else ""


def _rows(text: str) -> list[tuple[list[str], list[str]]]:
    """Every data row of every Markdown table in the text, with its header."""
    header: list[str] = []
    rows = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = []  # a new table below gets its own header
            continue
        cells = [cell.strip().strip("`").strip() for cell in stripped.strip("|").split("|")]
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


def index_rows(text: str) -> list[dict]:
    """Every row of the index, by column name, in the order it is written.

    The path-based way in. `read_index` reaches a project's index through
    `docker exec` and the row parsing behind it was private, so anything that
    already has the text — Phase 1's read API, reading a mounted checkout —
    had no way to it but a second parser. This is that way, and the two readers
    below go through it so there is still only one.

    `resolved` is best-effort by construction: see `_RESOLVED_RE`.
    """
    return [
        {
            "id": _column(cells, header, "id", 0),
            "what": (what := _column(cells, header, "what", 1)),
            "where": _column(cells, header, "where", 2),
            "fix": _column(cells, header, "fix", 3),
            "card": _column(cells, header, "card", -1),
            "resolved": bool(_RESOLVED_RE.match(what)),
        }
        for cells, header in _rows(text)
    ]


def index_fingerprints(text: str) -> set[str]:
    """What the index already holds, for deciding what is new.

    Only accepted debt gets a card, and only debt the index does not already
    describe: a project whose board fills with the same entry once per task is
    a board nobody reads.
    """
    return {fingerprint(row["what"]) for row in index_rows(text) if row["what"]}


def card_ids(text: str, entries: list[str]) -> dict[str, str]:
    """The card each of these entries points at, as the index records it.

    The lookup the merge path needs: a task's return names the entries it
    resolved, and the cards to close are whatever those rows say.
    """
    wanted = set(entries)
    found = {}
    for row in index_rows(text):
        entry, card = row["id"], row["card"]
        if entry in wanted and card and card.lower() not in ("-", "none", "n/a"):
            found[entry] = card
    return found


def resolved(payload: dict | None) -> list[str]:
    """The entry ids one handoff claims to have resolved."""
    seen = []
    for item in (payload or {}).get("resolved_debt") or []:
        entry = str(item or "").strip().strip("`").strip()
        if entry and entry not in seen:
            seen.append(entry)
    return seen


def filing_note(filed: list[tuple[str, str | None, Declaration]]) -> str:
    """What the auditor is told about the entries it is about to write.

    The ids are assigned here, before the phase runs, because the card exists
    before the row does: handing the auditor both ids lets it write the row
    that points at the card in the same pass, and keeps it the only writer of
    the index.
    """
    if not filed:
        return ""
    columns = " | ".join(COLUMNS)
    lines = [
        "The debt the revisor accepted in this task already has its ids, and its cards are "
        f"already on the board. File one row per entry in `{project_docs.DEBT_INDEX}`, with "
        f"these columns in this order: {columns}. Use the id given here — it is what the card "
        "points back at — and put the card's id in the `card` column, in backticks, verbatim. "
        "Then write the entry itself as a file under "
        f"`{project_docs.DEBT_DIR}/`, named after its id.",
        "",
    ]
    for entry, card, declaration in filed:
        card_text = f"card `{card}`" if card else "no card (this project has no board)"
        lines.append(f"- `{entry}` — {declaration.what} ({card_text})")
    lines.append("")
    lines.append(
        "Do not file anything else as debt: work the revisor did not accept is not an entry, "
        "and an entry with no id here has no card to point at."
    )
    return "\n".join(lines)
