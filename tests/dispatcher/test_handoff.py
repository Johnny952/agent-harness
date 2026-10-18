import dataclasses
import json
import re

import pytest

from dispatcher import handoff
from dispatcher.docker_exec import ClaudeResult

ROLES = ("cartografo", "arquitecto", "implementador", "revisor", "auditor")


def _result(text="", raw=None):
    return ClaudeResult(session_id="s-1", result_text=text, raw=raw if raw is not None else {})


def _structured(payload, text="Structured output provided successfully"):
    return _result(text=text, raw={"structured_output": payload})


# --- the schema -------------------------------------------------------------

@pytest.mark.parametrize("role", ROLES)
def test_schema_requires_every_field_it_declares(role) -> None:
    """An optional field is one the model drops when it has least to say —
    which is exactly when its emptiness is the fact worth handing over (no
    subagents left running, nothing verified). Empty arrays, not absence."""
    schema = handoff.schema_for(role)
    assert sorted(schema["required"]) == sorted(schema["properties"])


@pytest.mark.parametrize("role", ROLES)
def test_schema_is_closed_at_every_level(role) -> None:
    """`additionalProperties: false` throughout, so the CLI's strict-schema
    derivation has something to derive: a role that invents a field of its own
    has spent budget on something no reader parses."""
    schema = handoff.schema_for(role)
    assert schema["additionalProperties"] is False
    for prop in schema["properties"].values():
        item = prop.get("items")
        if isinstance(item, dict) and item.get("type") == "object":
            assert item["additionalProperties"] is False
            assert sorted(item["required"]) == sorted(item["properties"])


def test_only_the_revisor_gets_a_verdict_field() -> None:
    """The verdict is a gate, not a summary field. A verdict on the auditor's
    schema would invite a second one nothing reads."""
    assert "verdict" in handoff.schema_for("revisor")["properties"]
    for role in ("arquitecto", "implementador", "auditor"):
        assert "verdict" not in handoff.schema_for(role)["properties"]
    assert handoff.schema_for("revisor")["properties"]["verdict"]["enum"] == ["APPROVED", "CHANGES_REQUESTED"]


def test_only_the_revisor_gets_a_findings_field() -> None:
    """The lines the cycle compares between two reviews (ADR 73)."""
    findings = handoff.schema_for("revisor")["properties"]["findings"]
    assert findings["type"] == "array" and findings["items"] == {"type": "string"}
    assert "verbatim" in findings["description"]
    for role in ("arquitecto", "implementador", "auditor"):
        assert "findings" not in handoff.schema_for(role)["properties"]


def test_findings_of_reads_the_lines_and_tolerates_a_missing_field() -> None:
    assert handoff.findings_of({"findings": [" The route has no test. ", "", 3]}) == ["The route has no test.", "3"]
    assert handoff.findings_of({"status": "complete"}) == []
    assert handoff.findings_of(None) == []


def test_render_puts_the_findings_where_the_next_round_reads_them() -> None:
    rendered = handoff.render({"status": "complete", "verdict": "CHANGES_REQUESTED", "findings": ["The route has no test."]})
    assert "**Findings**\n- The route has no test." in rendered


def test_every_role_carries_the_fields_the_next_phase_reads() -> None:
    for role in ROLES:
        properties = handoff.schema_for(role)["properties"]
        for field in ("status", "changed", "verified", "pending", "risks", "subagents", "learnings", "debt", "paths"):
            assert field in properties, f"{role} is missing {field}"


@pytest.mark.parametrize("role", ROLES)
def test_no_schema_description_puts_debt_on_a_board(role) -> None:
    """The schema is prompt text every phase reads. The board seam is gone
    (`docs/decisions.md` ADR 59): accepted debt is filed in the index and
    nowhere else."""
    text = json.dumps(handoff.schema_for(role))
    assert "on the board" not in text
    assert "its card" not in text


def test_an_accepted_ruling_says_where_the_debt_is_filed() -> None:
    ruling = handoff.schema_for("revisor")["properties"]["debt_rulings"]["items"]["properties"]["ruling"]
    assert "accepted: it gets filed in the debt index." in ruling["description"]


def test_unknown_role_gets_no_schema() -> None:
    """No schema means no `--json-schema` flag and the prose path, the same
    way an unknown role gets no skills — not a crashed dispatch."""
    assert handoff.schema_for("becario") is None
    assert handoff.schema_for("") is None


@pytest.mark.parametrize("role", ROLES)
def test_schema_stays_small_enough_to_send_on_every_call(role) -> None:
    """The schema rides along on every call the role makes, descriptions
    included, so it is charged like an always-on skill. This is the guard on
    growing it by half a page of prose per field.

    The cap was 3072 until the debt flow landed: `debt` is six fields, each
    needing a line saying what belongs in it, and the revisor carries a ruling
    per declaration on top. That is the shape of the feature, not prose, so
    the number moved once — with ~450 bytes of headroom left on the widest
    role, which is still less than one more field's worth."""
    assert len(json.dumps(handoff.schema_for(role), separators=(",", ":")).encode()) < 4096


def test_the_arquitecto_is_offered_a_split_and_has_to_answer_it() -> None:
    """Offering the field is the switch (`docs/decisions.md` ADR 60). Required
    like every other field, so "nothing to split" is an empty list the
    arquitecto wrote and not a key it left out."""
    schema = handoff.schema_for("arquitecto")
    assert schema["properties"]["split"] == handoff._SPLIT_PROPERTY["split"]
    assert "split" in schema["required"]


@pytest.mark.parametrize("role", [role for role in ROLES if role != "arquitecto"])
def test_no_other_role_is_offered_a_split(role) -> None:
    """Only the cycle's branch after the arquitecto reads one: a split from
    any later phase would be printed into the task file and acted on by
    nothing."""
    schema = handoff.schema_for(role)
    assert "split" not in schema["properties"]
    assert "split" not in schema["required"]


def test_the_split_property_is_closed_like_the_other_object_lists() -> None:
    """Checked on the property itself, whichever roles carry it."""
    item = handoff._SPLIT_PROPERTY["split"]["items"]
    assert item["additionalProperties"] is False
    assert sorted(item["required"]) == sorted(item["properties"])
    assert sorted(item["properties"]) == ["after", "description", "surface", "title"]


# --- budgets ----------------------------------------------------------------

def test_reviewing_roles_get_a_smaller_budget_than_writing_ones() -> None:
    assert handoff.budget_for("revisor") < handoff.budget_for("implementador")
    assert handoff.budget_for("auditor") < handoff.budget_for("revisor")


def test_unknown_role_still_gets_a_budget() -> None:
    assert handoff.budget_for("becario") > 0


def test_the_mapper_gets_as_little_room_as_any_role() -> None:
    """The mapping phase's output is the docs it wrote, in the project's tree.
    Its handoff only has to say where it got to, so a long one is budget spent
    summarising files the next phase can open for itself."""
    mapper = handoff.budget_for("cartografo")
    assert mapper == min(handoff.budget_for(role) for role in ROLES)
    assert mapper < handoff.budget_for("arquitecto")


def test_every_budget_buys_enough_lines_to_report_with() -> None:
    """The budgets are stated to the roles in entries, so a budget is only
    sane if the entries it buys are enough to say what the role saw. The
    shortest real auditor handoff on record ran to 22 of them."""
    for role in ROLES:
        assert handoff.lines_for(role) >= 12, role
    assert handoff.lines_for("auditor") >= 22


def test_every_budget_covers_what_its_role_filed_on_a_real_repo() -> None:
    """T-008 is the first task run against a repo that is not the toy one, and
    every one of its five phases went over the budget it had then. These are
    the sizes the accepted returns measured — what the next phase actually
    read, after the one rewrite a role gets — so a budget under them is one
    that will be missed again. The revisor is its round two, the larger of the
    two it filed."""
    filed = {"arquitecto": 4732, "implementador": 6313, "revisor": 4280, "auditor": 2964}
    for role, measured in filed.items():
        assert handoff.budget_for(role) >= measured, role


def test_an_unknown_role_gets_countable_lines_too() -> None:
    assert handoff.lines_for("becario") > 0


def test_lines_track_the_budget() -> None:
    """The two numbers in the prompt describe one limit, so they cannot be
    allowed to drift: the entries are derived from the bytes."""
    ordered = sorted(ROLES, key=handoff.budget_for)
    assert [handoff.lines_for(role) for role in ordered] == sorted(
        handoff.lines_for(role) for role in ordered
    )


# --- the margin -------------------------------------------------------------

def test_a_handoff_that_fits_is_never_worth_shrinking() -> None:
    assert not handoff.worth_shrinking("revisor", 0)


def test_a_small_overage_is_not_worth_a_model_call() -> None:
    """Five of the nineteen overages on record were under 300 bytes — a line
    and a half in a file nobody was struggling to read, for the price of a
    `--resume`."""
    assert not handoff.worth_shrinking("arquitecto", 46)
    assert not handoff.worth_shrinking("revisor", 67)
    assert not handoff.worth_shrinking("arquitecto", 170)
    assert not handoff.worth_shrinking("implementador", 280)
    assert not handoff.worth_shrinking("revisor", 184)


def test_a_page_of_prose_over_is_worth_a_model_call() -> None:
    """The overages worth paying for are the ones where the handoff is
    carrying detail that belongs in a file."""
    assert handoff.worth_shrinking("arquitecto", 721)
    assert handoff.worth_shrinking("implementador", 1439)
    assert handoff.worth_shrinking("auditor", 1018)


def test_the_margin_is_a_share_of_the_budget_not_a_flat_count() -> None:
    """A tight budget should tolerate fewer bytes than a loose one; a floor
    keeps the tightest budgets from spending a call on a few dozen."""
    assert handoff.worth_shrinking("cartografo", 340)
    assert not handoff.worth_shrinking("implementador", 300)
    assert not handoff.worth_shrinking("cartografo", 200), "the floor holds below the share"


# --- parsing ----------------------------------------------------------------

def test_parse_reads_the_envelope_field_not_the_result_text() -> None:
    """With a schema in play the CLI can leave `result` holding a placeholder,
    so the payload is read from `structured_output` or not at all."""
    payload = {"status": "complete"}
    assert handoff.parse(_structured(payload)) == payload


def test_parse_prefers_the_envelope_over_json_in_the_text() -> None:
    assert handoff.parse(
        _result(text='{"status": "blocked"}', raw={"structured_output": {"status": "complete"}})
    ) == {"status": "complete"}


def test_parse_accepts_a_json_object_in_the_text() -> None:
    """The fallback for a phase that produced no envelope field — an older
    image, or a role dispatched without a schema that answered in JSON
    anyway."""
    assert handoff.parse(_result(text='  {"status": "partial"}  ')) == {"status": "partial"}


def test_parse_accepts_a_fenced_json_object() -> None:
    text = '```json\n{"status": "complete"}\n```'
    assert handoff.parse(_result(text=text)) == {"status": "complete"}


def test_parse_returns_none_for_prose() -> None:
    assert handoff.parse(_result(text="I reviewed the diff and it looks fine.")) is None


def test_parse_returns_none_for_json_that_is_not_an_object() -> None:
    """A bare list or number parses as JSON but has no fields to read; the
    caller must fall through to the prose path rather than get a payload it
    cannot index."""
    assert handoff.parse(_result(text="[1, 2, 3]")) is None
    assert handoff.parse(_result(text="42")) is None


def test_parse_survives_an_empty_envelope() -> None:
    assert handoff.parse(ClaudeResult(session_id=None, result_text="", raw={})) is None


# --- budget enforcement -----------------------------------------------------

def test_measure_counts_bytes_not_characters() -> None:
    """The budget is in bytes because that is what the task file and the next
    phase's context actually cost; an accented summary is not free."""
    assert handoff.measure(_result(text="é" * 10)) == 20


def test_measure_of_a_structured_return_ignores_the_placeholder_text() -> None:
    """What lands in the task file is the payload, so that is what is
    measured — charging the role for the CLI's own placeholder would fail
    budgets nobody blew."""
    payload = {"status": "complete"}
    measured = handoff.measure(_structured(payload, text="x" * 5000))
    assert measured == len(json.dumps(payload, separators=(",", ":")).encode())


def test_over_budget_is_zero_when_it_fits() -> None:
    assert handoff.over_budget("revisor", _structured({"status": "complete"})) == 0


def test_over_budget_reports_the_overage() -> None:
    payload = {"status": "complete", "risks": ["r" * (handoff.budget_for("revisor") + 1024)]}
    overage = handoff.over_budget("revisor", _structured(payload))
    assert overage == handoff.measure(_structured(payload)) - handoff.budget_for("revisor")
    assert overage > 0


def test_a_long_prose_return_is_over_budget_too() -> None:
    """A role that ignored the schema is the loudest case there is; it has to
    be asked to shorten like any other."""
    assert handoff.over_budget("auditor", _result(text="x" * 9000)) > 0


def test_shrink_prompt_names_the_size_and_the_budget() -> None:
    prompt = handoff.shrink_prompt("revisor", 9000, 3072)
    assert "9000" in prompt and "3072" in prompt and "revisor" in prompt


# --- the verdict ------------------------------------------------------------

def test_verdict_of_reads_the_field() -> None:
    assert handoff.verdict_of({"verdict": "APPROVED"}) == handoff.APPROVED
    assert handoff.verdict_of({"verdict": "CHANGES_REQUESTED"}) == handoff.CHANGES_REQUESTED


def test_verdict_of_normalizes_case_and_padding() -> None:
    """The enum says uppercase, but a payload that arrived through the prose
    fallback never went through the CLI's validation."""
    assert handoff.verdict_of({"verdict": " approved \n"}) == handoff.APPROVED


def test_verdict_of_is_none_when_there_is_no_verdict() -> None:
    """None, not CHANGES_REQUESTED: absence means "read the text instead",
    and it is the text path that fails closed."""
    assert handoff.verdict_of(None) is None
    assert handoff.verdict_of({}) is None
    assert handoff.verdict_of({"verdict": ""}) is None
    assert handoff.verdict_of({"verdict": ["APPROVED"]}) is None


# --- the block --------------------------------------------------------------

def test_blocked_reads_the_status() -> None:
    assert handoff.blocked({"status": handoff.BLOCKED}) is True
    assert handoff.blocked({"status": "complete"}) is False
    assert handoff.blocked({"status": "partial"}) is False


def test_blocked_normalizes_case_and_padding() -> None:
    """Same reason as the verdict: a payload that came back through the prose
    fallback never went through the CLI's enum."""
    assert handoff.blocked({"status": " Blocked \n"}) is True


def test_blocked_is_false_when_there_is_no_status() -> None:
    """False, not True: a phase that said nothing ran, and a missing field is
    not a role asking a human for a decision."""
    assert handoff.blocked(None) is False
    assert handoff.blocked({}) is False
    assert handoff.blocked({"status": ""}) is False
    assert handoff.blocked({"status": ["blocked"]}) is False


def test_pending_reads_the_lines() -> None:
    assert handoff.pending({"pending": ["what the empty state shows", " ", ""]}) == [
        "what the empty state shows",
    ]
    assert handoff.pending(None) == []
    assert handoff.pending({}) == []
    assert handoff.pending({"pending": "one string"}) == []


# --- the split --------------------------------------------------------------

def _entry(n, after=()):
    return handoff.SubTask(
        title=f"part {n}", surface=f"surface {n}", description=f"build part {n}", after=tuple(after),
    )


def _subtasks(n):
    """A split of `n` entries nothing is wrong with, each after the one before."""
    return [_entry(i, after=(i - 1,) if i > 1 else ()) for i in range(1, n + 1)]


def test_split_of_is_empty_when_there_is_no_split() -> None:
    assert handoff.split_of(None) == []
    assert handoff.split_of({}) == []
    assert handoff.split_of({"split": []}) == []
    assert handoff.split_of({"split": "one string"}) == []


def test_split_of_returns_the_entries_in_order() -> None:
    """The description is kept to the byte: it becomes the sub-task's own task
    description, and a re-filed split is compared against what was written."""
    subtasks = handoff.split_of({"split": [
        {"title": " api route ", "surface": " GET /api/x ", "description": "Build the route.\n", "after": []},
        {"title": "screen", "surface": "the X screen", "description": "Build the screen.", "after": [1]},
    ]})
    assert subtasks == [
        handoff.SubTask(title="api route", surface="GET /api/x", description="Build the route.\n", after=()),
        handoff.SubTask(title="screen", surface="the X screen", description="Build the screen.", after=(1,)),
    ]
    assert isinstance(subtasks[1].after, tuple)


def test_split_of_skips_entries_that_are_not_objects() -> None:
    subtasks = handoff.split_of({"split": [
        "bare string",
        {"title": "a", "surface": "s", "description": "d", "after": []},
        None,
        {"title": "b", "surface": "s", "description": "d", "after": [1]},
    ]})
    assert [subtask.title for subtask in subtasks] == ["a", "b"]


def test_split_of_keeps_what_it_cannot_read_for_split_problems() -> None:
    """Never repaired into something valid: an `after` that was dropped is a
    sub-task started before the work it reads."""
    subtasks = handoff.split_of({"split": [
        {"title": None, "surface": 3, "description": ["x"]},
        {"title": "b", "surface": "s", "description": "d", "after": "1"},
        {"title": "c", "surface": "s", "description": "d", "after": None},
    ]})
    assert subtasks[0] == handoff.SubTask(title="", surface="", description="", after=())
    assert subtasks[1].after == ("1",)
    assert subtasks[2].after == ()
    assert handoff.split_problems(subtasks)


def test_a_subtask_cannot_be_changed_once_read() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _entry(1).title = "something else"


@pytest.mark.parametrize("count", [2, 3, 4])
def test_split_problems_is_empty_for_a_split_that_can_be_filed(count) -> None:
    assert handoff.split_problems(_subtasks(count)) == []


@pytest.mark.parametrize("count", [0, 1, 5])
def test_split_problems_refuses_a_split_of_the_wrong_size(count) -> None:
    """One entry is a plan with extra steps; five means the task was a
    project, and that cut is the operator's."""
    problems = handoff.split_problems(_subtasks(count))
    assert len(problems) == 1
    assert "2 to 4" in problems[0] and str(count) in problems[0]


@pytest.mark.parametrize("field", ["title", "surface", "description"])
def test_split_problems_refuses_a_blank_field(field) -> None:
    subtasks = _subtasks(2)
    subtasks[1] = dataclasses.replace(subtasks[1], **{field: " \n"})
    problems = handoff.split_problems(subtasks)
    assert len(problems) == 1
    assert "sub-task 2" in problems[0] and field in problems[0]


@pytest.mark.parametrize("after", [(2,), (3,), (0,), (-1,)], ids=["itself", "later", "zero", "negative"])
def test_split_problems_refuses_an_after_that_is_not_an_earlier_entry(after) -> None:
    """Backward-only is what makes a cycle impossible by construction."""
    subtasks = [_entry(1), _entry(2, after=after), _entry(3)]
    problems = handoff.split_problems(subtasks)
    assert len(problems) == 1
    assert "sub-task 2" in problems[0] and "after" in problems[0] and str(after[0]) in problems[0]


def test_split_problems_refuses_a_duplicate_after_once() -> None:
    subtasks = [_entry(1), _entry(2), _entry(3, after=(1, 2, 1, 1))]
    problems = handoff.split_problems(subtasks)
    assert len(problems) == 1
    assert "sub-task 3" in problems[0] and "more than once" in problems[0]


@pytest.mark.parametrize("item", ["1", True, 1.0, None, {}], ids=repr)
def test_split_problems_refuses_an_after_that_is_not_an_integer(item) -> None:
    """`True` is not position 1, and an unhashable item must not raise: this
    is the only check between a prose-fallback payload and a filed sub-task."""
    subtasks = [_entry(1), _entry(2, after=(item,))]
    problems = handoff.split_problems(subtasks)
    assert len(problems) == 1
    assert "sub-task 2" in problems[0] and "integer" in problems[0]


def test_split_problems_reports_every_problem_not_the_first() -> None:
    """A bad split ends the task as a block, and a block that names one reason
    of three costs two more arquitecto phases to learn the rest."""
    subtasks = [dataclasses.replace(_entry(1), title=""), _entry(2, after=(2,))]
    problems = handoff.split_problems(subtasks)
    assert len(problems) == 2
    assert "sub-task 1" in problems[0] and "title" in problems[0]
    assert "sub-task 2" in problems[1] and "after" in problems[1]


# --- rendering --------------------------------------------------------------

def test_render_drops_empty_sections() -> None:
    """The next role pays for every line of this; `Risks: (none)` costs the
    same as a risk."""
    rendered = handoff.render({
        "status": "complete", "changed": ["dispatcher/handoff.py"],
        "verified": [], "pending": [], "risks": [], "subagents": [],
        "learnings": [], "debt": [], "paths": [],
    })
    assert "**Changed**" in rendered
    for absent in ("Verified", "Pending", "Risks", "Subagents", "Detail", "Proposed"):
        assert absent not in rendered


def test_render_puts_status_and_verdict_on_the_first_line() -> None:
    rendered = handoff.render({"status": "partial", "verdict": "CHANGES_REQUESTED"})
    assert rendered.splitlines()[0] == "**Status:** partial · **Verdict:** CHANGES_REQUESTED"


def test_render_carries_subagent_ids_and_what_each_was_doing() -> None:
    """These are what let a later phase revive a subagent instead of paying to
    respawn it, so they survive the summary."""
    rendered = handoff.render({"subagents": [{"id": "ag_42", "doing": "running the suite"}]})
    assert "`ag_42` — running the suite" in rendered


def test_render_carries_paths_to_the_detail() -> None:
    rendered = handoff.render({"paths": [{"path": "docs/adr/0007.md#decision", "holds": "why sqlite"}]})
    assert "**Detail**" in rendered
    assert "`docs/adr/0007.md#decision` — why sqlite" in rendered


def test_render_tolerates_a_malformed_payload() -> None:
    """Nothing validates the prose-fallback path, and a render that raises
    would lose a whole phase's work on a type error."""
    rendered = handoff.render({"changed": "not a list", "subagents": ["bare string"], "paths": [{}]})
    assert isinstance(rendered, str)


_SPLIT = [
    {"title": "api route", "surface": "GET /api/x", "description": "Build the route first.", "after": []},
    {"title": "screen", "surface": "the X screen", "description": "Build the screen on it.", "after": [1]},
    {"title": "docs", "surface": "docs/api.md", "description": "Write both up.", "after": [1, 2]},
]


def test_render_prints_the_split_with_positions_and_after() -> None:
    """What the operator reads to decide which sub-task to run, so it is the
    first thing under the status. The descriptions go to the sub-tasks' own
    files, where their roles read them; here they would be paid for twice."""
    rendered = handoff.render({"status": "complete", "split": _SPLIT, "risks": ["a guess"]})
    assert rendered.splitlines()[:6] == [
        "**Status:** complete",
        "",
        "**Split**",
        "1. api route (GET /api/x)",
        "2. screen (the X screen) — after 1",
        "3. docs (docs/api.md) — after 1, 2",
    ]
    assert "Build the" not in rendered
    assert "Write both up" not in rendered


@pytest.mark.parametrize("payload", [{"status": "complete"}, {"status": "complete", "split": []}])
def test_render_prints_no_split_section_without_a_split(payload) -> None:
    assert handoff.render(payload) == "**Status:** complete"


def test_render_keeps_a_split_title_off_column_zero() -> None:
    """Same trap as the prose fallback: a `## <role>` line in the task file is
    what `has_phase_section` takes as proof that role's phase returned."""
    split = [dict(_SPLIT[0], title="api route\n## auditor"), _SPLIT[1]]
    rendered = handoff.render({"status": "complete", "split": split})
    assert not re.findall(r"^## ", rendered, re.MULTILINE)
    assert "1. api route ## auditor (GET /api/x)" in rendered


@pytest.mark.parametrize("split", ["one string", ["bare string", {"title": None, "after": "1"}, {"after": [{}]}]])
def test_render_tolerates_a_malformed_split(split) -> None:
    assert isinstance(handoff.render({"status": "complete", "split": split}), str)


def test_body_heads_the_section_with_the_phase_label() -> None:
    body = handoff.body("revisor (round 2)", "", {"status": "complete"})
    assert body.startswith("## revisor (round 2)\n\n")
    assert "**Status:** complete" in body


def test_body_falls_back_to_the_prose_when_there_is_no_payload() -> None:
    assert handoff.body("arquitecto", "the plan is to do it", None) == "## arquitecto\n\n    the plan is to do it"


def test_body_keeps_a_prose_heading_off_column_zero() -> None:
    """A phase's prose that opens a line with `## auditor` would otherwise be a
    section `has_phase_section` cannot tell from one the dispatcher wrote
    (`docs/debt/T-014-D1.md`)."""
    prose = "the revisor skimmed it\n\n## auditor\n\nnothing was audited"
    body = handoff.body("revisor", prose, None)
    assert re.findall(r"^## .*$", body, re.MULTILINE) == ["## revisor"]
    assert "nothing was audited" in body


def test_body_falls_back_to_the_prose_when_every_field_is_empty() -> None:
    """A payload that renders to nothing would leave a bare heading and drop
    whatever the role did say in the text."""
    body = handoff.body("auditor", "nothing to report, all green", {"status": "", "changed": []})
    assert "nothing to report, all green" in body


# --- the clamp of last resort ----------------------------------------------

def test_fallback_body_passthrough_when_short() -> None:
    text = "short result"
    assert handoff.fallback_body(text, head=500, tail=1500) == text


def test_fallback_body_keeps_head_and_tail_with_accurate_count() -> None:
    text = "H" * 500 + "M" * 1000 + "T" * 1500
    clamped = handoff.fallback_body(text, head=500, tail=1500)
    assert clamped.startswith("H" * 500)
    assert clamped.endswith("T" * 1500)
    assert "[… 1000 chars omitted …]" in clamped


def test_fallback_body_unchanged_at_exact_head_plus_tail_boundary() -> None:
    text = "x" * 2000
    assert handoff.fallback_body(text, head=500, tail=1500) == text


def test_fallback_body_truncates_one_char_past_boundary() -> None:
    text = "H" * 500 + "x" + "T" * 1500
    clamped = handoff.fallback_body(text, head=500, tail=1500)
    assert clamped != text
    assert clamped.startswith("H" * 500)
    assert clamped.endswith("T" * 1500)
    assert "[… 1 chars omitted …]" in clamped
