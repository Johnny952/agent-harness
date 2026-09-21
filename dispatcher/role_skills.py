# dispatcher/role_skills.py
"""Which vendored skills each role gets, and how they reach the CLI.

The skills themselves live in `skills/` in this repo and are baked into the
agent image at SKILLS_ROOT (see docker/agent/Dockerfile). They are handed to
the CLI per call, one `--plugin-dir` per skill, rather than installed into
the shared /root/.claude: that volume tracks no version, so a skill installed
there would drift inside running containers independently of the image that
was tested — the same defect class as the CLI auto-updater. Delivered per
call, a role reads the skills for its job and not the others', and the set it
got is whatever the image it ran on shipped.

`skills/README.md` holds the same table in prose, plus the token cost of each
role's set. The two must agree; tests/dispatcher/test_role_skills.py parses
the README and checks that they do.
"""
from __future__ import annotations

#: Where docker/agent/Dockerfile copies the `skills/` tree to. Not a config
#: key: it is a property of the image, not of a deployment, and a wrong value
#: could not be fixed by editing config.yaml anyway — the tree only exists
#: where the Dockerfile put it.
SKILLS_ROOT = "/opt/ia-harness/skills"

#: Role -> the skills delivered to it, in the order the README table lists
#: them. A role missing from this map gets none, which is the right answer for
#: anything that is not one of the four phases.
ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    "arquitecto": (
        "writing-plans",
        "minimal-scope",
        "systematic-debugging",
    ),
    "implementador": (
        "test-driven-development",
        "minimal-scope",
        "systematic-debugging",
        "receiving-code-review",
        "verification-before-completion",
    ),
    "revisor": (
        "systematic-debugging",
        "verification-before-completion",
        "blocking-review",
    ),
    "auditor": (
        "systematic-debugging",
        "code-quality-review",
    ),
}


def skills_for(role: str) -> tuple[str, ...]:
    return ROLE_SKILLS.get(role, ())


def plugin_dirs(role: str) -> list[str]:
    """The `--plugin-dir` arguments for this role, one per skill.

    A path that does not exist in the image — an agent container still
    running an image built before the skills were baked in — is not fatal:
    the CLI prints `✘ Path not found: <dir>` and runs the phase anyway
    (verified against 2.1.273). So an old image degrades to no skills rather
    than failing every dispatch, and this needs no guard.
    """
    return [f"{SKILLS_ROOT}/{name}" for name in skills_for(role)]


#: Charged on every call the role makes, so it stays short. It says three
#: things the skills themselves cannot: which of them belong to this phase,
#: that they are method rather than domain, and who wins when they disagree
#: with the project (the project does — a skill vendored here knows nothing
#: about the repo the role was dispatched against).
_PRECEDENCE = (
    "They describe method, not this project: where the project's own docs, "
    "conventions or .claude/skills differ about its domain, the project wins."
)


def system_prompt(role: str) -> str | None:
    """The `--append-system-prompt` text for this role, or None if it has no
    skills — in which case there is nothing to say and nothing to charge."""
    skills = skills_for(role)
    if not skills:
        return None
    return (
        f"Method skills delivered for this {role} phase: "
        f"{', '.join(skills)}. Use them when they apply. {_PRECEDENCE}"
    )
