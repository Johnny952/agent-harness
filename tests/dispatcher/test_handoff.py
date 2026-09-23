import json

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


def test_every_role_carries_the_fields_the_next_phase_reads() -> None:
    for role in ROLES:
        properties = handoff.schema_for(role)["properties"]
        for field in ("status", "changed", "verified", "pending", "risks", "subagents", "learnings", "debt", "paths"):
            assert field in properties, f"{role} is missing {field}"


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
    payload = {"status": "complete", "risks": ["r" * 4000]}
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


def test_body_heads_the_section_with_the_phase_label() -> None:
    body = handoff.body("revisor (round 2)", "", {"status": "complete"})
    assert body.startswith("## revisor (round 2)\n\n")
    assert "**Status:** complete" in body


def test_body_falls_back_to_the_prose_when_there_is_no_payload() -> None:
    assert handoff.body("arquitecto", "the plan is to do it", None) == "## arquitecto\n\nthe plan is to do it"


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
