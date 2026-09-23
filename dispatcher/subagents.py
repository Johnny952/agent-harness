# dispatcher/subagents.py
"""The subagents a phase started, and the rule for picking one back up.

A role that delegates work to a subagent and then loses its phase — to a rate
limit, to the turn budget, to the gates asking for a fix — used to start that
subagent again from nothing. The replacement re-derives context the original
still held and has to be briefed a second time, at full quota cost.

It does not have to. A subagent killed with its parent survives in the CLI's
own state and is revived by its raw agent id with the SendMessage tool, with
its instructions and every completed turn intact; the D1 gate in
`docs/ROADMAP.md` proves it works even when the parent session is resumed on
a *different* account, because the per-agent transcript lives in the shared
`claude_shared` volume both agent containers mount.

What makes that usable is that the ids are readable from disk. The Agent
tool's own result tells the model never to quote an agent id back to the
user, so asking the role for them is asking it to break its instructions; and
`exec_claude` runs with `--output-format json`, so there is no event stream to
read them from either. The CLI does leave one file per subagent next to the
session transcript, with the id in its filename — that is the source this
module reads, and it needs no cooperation from the model at all.

One thing disk cannot say is which of them are still running. It does not
have to: the CLI announces a subagent that outlived its session by itself, at
resume, as a `task_notification` the role sees. So this module answers "what
did this session start", and the note it builds tells the role what to do
about whichever one the CLI names.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections.abc import Sequence

from dispatcher import docker_exec

logger = logging.getLogger(__name__)

#: Where the CLI keeps its per-session state inside the agent containers. A
#: property of the image, like SKILLS_ROOT: entrypoint.sh symlinks
#: /root/.claude-account/projects here, and this is the path the `claude_shared`
#: volume is mounted at, which is why one account can read what the other
#: started.
PROJECTS_ROOT = "/root/.claude/projects"

#: The component between PROJECTS_ROOT and the session id is the CLI's
#: encoding of the cwd the session ran in (`/data/projects` -> `-data-projects`),
#: so it differs per worktree. Globbing it beats reproducing the encoding: the
#: session id below it is already unique.
_META_GLOB = "agent-*.meta.json"
_PREFIX = "agent-"
_SUFFIX = ".meta.json"

#: The id goes into an `sh -c` string, so it is checked against the shape the
#: CLI actually writes before it gets there. Anything else is refused rather
#: than quoted: a session id is not operator input, and one that does not look
#: like a uuid means something upstream is wrong, not that this glob should be
#: creative.
_SESSION_ID = re.compile(r"\A[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")

#: Listing a directory is not phase work: it gets its own short budget rather
#: than the configured phase timeout.
_HARVEST_TIMEOUT_SECONDS = 30


@dataclasses.dataclass(frozen=True)
class Subagent:
    """One subagent a session started, as its meta file describes it."""

    #: The raw agent id SendMessage addresses. It is the filename, not a
    #: field: the file is named after the agent.
    id: str
    #: The `description` the phase passed the Agent tool — one line, which is
    #: exactly what a revive note needs to say which agent is which.
    description: str
    agent_type: str


def of_session(container: str, session_id: str | None) -> list[Subagent]:
    """Every subagent that session started, newest state on disk.

    Swallows everything, like the gates do: an id the dispatcher could not
    read costs a respawn, which is what happened before this existed. It must
    never cost a phase.
    """
    if not session_id or not _SESSION_ID.match(session_id):
        if session_id:
            logger.warning("not a session id, so no subagents were looked up: %r", session_id)
        return []
    # One line per agent: `agent-<id>.meta.json<TAB><the file, newlines stripped>`.
    # `[ -f ]` is what makes a glob that matched nothing quiet instead of
    # returning the pattern itself.
    script = (
        f'for f in {PROJECTS_ROOT}/*/{session_id}/subagents/{_META_GLOB}; do '
        '[ -f "$f" ] || continue; '
        'printf "%s\\t" "${f##*/}"; '
        'tr -d "\\n" < "$f"; '
        'printf "\\n"; '
        "done"
    )
    try:
        proc = docker_exec.run_docker_exec(
            container, "/", ["sh", "-c", script], timeout=_HARVEST_TIMEOUT_SECONDS
        )
    except Exception:
        logger.exception("could not read the subagents of session %s", session_id)
        return []
    if proc.returncode != 0:
        logger.warning(
            "reading the subagents of session %s exited %d", session_id, proc.returncode
        )
        return []
    return sorted(_parse(proc.stdout), key=lambda agent: agent.id)


def _parse(stdout: str) -> list[Subagent]:
    found = []
    for line in (stdout or "").splitlines():
        name, tab, blob = line.partition("\t")
        if not tab or not name.startswith(_PREFIX) or not name.endswith(_SUFFIX):
            continue
        agent_id = name[len(_PREFIX) : -len(_SUFFIX)]
        if not agent_id:
            continue
        try:
            meta = json.loads(blob)
        except json.JSONDecodeError:
            # The id is in the filename, so a meta file being unreadable costs
            # the description and not the agent.
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        found.append(
            Subagent(
                id=agent_id,
                description=str(meta.get("description") or "").strip(),
                agent_type=str(meta.get("agentType") or "").strip(),
            )
        )
    return found


def revive_note(agents: Sequence[Subagent]) -> str:
    """What a resumed phase is told about the subagents it left behind.

    Only ever added to a prompt that resumes a session — a fresh session has
    no subagents of its own to revive, and SendMessage cannot reach another
    session's. It is deliberately not part of `role_skills.system_prompt`
    either: that text is charged on every call, and this is only true on the
    one call in a phase's life that picks a session back up.
    """
    if not agents:
        return ""
    listed = "\n".join(
        f"- `{agent.id}`" + (f" — {agent.description}" if agent.description else "")
        for agent in agents
    )
    return (
        "This session started the subagents below. If one of them is reported as not having "
        "finished, revive it: send it a message with the SendMessage tool addressed to its raw "
        "id (both `to` and `recipient`), saying what you now need from it. A revived subagent "
        "still holds its original instructions and every turn it completed, so it carries on; a "
        "fresh one starts cold and has to be briefed again, at your cost. Spawn a replacement "
        "only if the revive fails.\n"
        f"{listed}"
    )


def backfill(payload: dict | None, agents: Sequence[Subagent]) -> dict | None:
    """Put the real ids in the handoff, over whatever the phase said.

    The phase cannot be the source here: the Agent tool instructs it not to
    surface agent ids, so what it returns in this field is either empty or
    invented. Disk knows, and the `description` on the meta file is the one
    the phase itself passed the Agent tool, so nothing is lost by taking both
    from there.

    A phase that answered in prose has no payload to fill; its subagents are
    still on disk for a human to find, and there is no later reader to serve.
    """
    if payload is None or not agents:
        return payload
    return {
        **payload,
        "subagents": [{"id": agent.id, "doing": agent.description} for agent in agents],
    }
