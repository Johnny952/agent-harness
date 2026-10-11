"""No instruction asks a phase to write out its reasoning.

Anthropic's guide to Opus 5.5 says an instruction to think step by step or to
put the reasoning in the answer can now trigger a refusal, and the thinking it
asks for already happens on its own (`docs/plans/models-5-5.md`, Decisions 4).
A search of `skills/` and `dispatcher/` found none on 2026-10-11; this keeps it
that way, over the vendored skills and over every text the dispatcher itself
hands a phase.
"""

import re
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import handoff, project_docs, role_skills

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# Each names an ask for the reasoning itself, not a mention of it: "push back
# with technical reasoning" and "watch for these in your own reasoning" are
# fine, and stay fine.
_REASONING_ASKS = [
    r"think(ing)?\s+(it\s+)?(through\s+)?step[\s-]+by[\s-]+step",
    r"step[\s-]+by[\s-]+step\s+(reasoning|thinking)",
    r"chain[\s-]+of[\s-]+thought",
    r"think\s+(out\s+loud|aloud)",
    r"(show|explain|write\s+(out|down)|output|print|include|state|describe|share)\s+"
    r"(all\s+)?(your|the)\s+(full\s+)?(reasoning|thinking|thought\s+process|chain\s+of\s+thought)",
    r"<\s*/?\s*(thinking|reasoning|scratchpad)\s*>",
    r"before\s+(you\s+)?(answer|respond|reply)(ing)?,?\s+(first\s+)?(think|reason)",
]
_ASK = re.compile("|".join(f"(?:{p})" for p in _REASONING_ASKS), re.IGNORECASE)

_ROLES = (project_docs.MAPPER_ROLE, *role_skills.ROLE_SKILLS)


def _asks(text: str) -> list[str]:
    return [m.group(0) for m in _ASK.finditer(text)]


@pytest.mark.parametrize(
    "path",
    sorted(p for p in SKILLS_DIR.rglob("*") if p.is_file() and p.suffix in {".md", ".json", ".sh"}),
    ids=lambda p: str(p.relative_to(SKILLS_DIR)),
)
def test_no_vendored_skill_asks_for_the_reasoning(path: Path) -> None:
    assert _asks(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("role", _ROLES)
def test_no_prompt_the_dispatcher_builds_asks_for_the_reasoning(role: str, tmp_path: Path) -> None:
    texts = [
        dispatcher_mod._role_prompt(
            role, "T-1", "myproj", "/hive/T-1.md",
            "## Goal\n\nDo the thing.\n\n## Done when\n\nIt is done.\n",
            str(tmp_path / "scratch"),
            str(tmp_path / "hive"), round_num=3, max_turns=40, note="",
        ),
        dispatcher_mod._phase_system_prompt(role) or "",
        handoff.continue_prompt(),
        handoff.shrink_prompt(role, 9000, 4096),
        handoff.discarded_writes_prompt(role, ["src/x.py"]),
    ]
    assert [a for text in texts for a in _asks(text)] == []


@pytest.mark.parametrize(
    "text",
    [
        "Think step by step before you write the plan.",
        "Explain your reasoning in the handoff.",
        "Write out your thought process first.",
        "Put it in <thinking> tags.",
        "Use chain-of-thought.",
        "Before answering, think about the edge cases.",
    ],
)
def test_the_check_catches_an_ask_for_the_reasoning(text: str) -> None:
    assert _asks(text)


@pytest.mark.parametrize(
    "text",
    [
        "Push back with technical reasoning if wrong.",
        "Watch for these in your own reasoning.",
        "Write the plan as numbered steps.",
    ],
)
def test_the_check_lets_a_mention_of_reasoning_through(text: str) -> None:
    assert _asks(text) == []
