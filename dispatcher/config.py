from __future__ import annotations

import dataclasses

import yaml


@dataclasses.dataclass
class AccountConfig:
    name: str
    container: str
    #: Whether this is the account the conversational thread runs under. It is
    #: a sort key, not a kind of account: the pool is ordered, not partitioned,
    #: and the primary is simply the one the picker reaches last. Set by
    #: `load_config` from the top-level `primary_account` and never from the
    #: account's own block — see `_load_primary_account` for why there is only
    #: one place to name it.
    is_primary: bool = False


#: What the dispatcher's own status strings become on the board. The left side
#: is this harness's vocabulary (`_update_task_status` sends "blocked", "done"
#: and "in_progress:<role>", of which only the prefix is mapped); the right
#: side has to match a status *name* on the Vibe Kanban project, which is
#: per-project and which no MCP tool lists — so these are defaults for the
#: names Vibe Kanban ships with, and an operator whose project renamed them
#: says so in `vibe_kanban.status_map`. "blocked" maps to review because the
#: dispatcher blocks a task exactly when a human has to look at it.
DEFAULT_KANBAN_STATUS_MAP = {
    "in_progress": "In Progress",
    "blocked": "In Review",
    "done": "Done",
}


@dataclasses.dataclass
class VibeKanbanConfig:
    """How to reach a Vibe Kanban board, when there is one.

    `command` is an argv the dispatcher spawns and talks MCP to over stdio —
    Vibe Kanban's server has no SSE endpoint, so there is no URL to point at.
    `project_id` is the uuid of the project new issues land in; it is optional
    because the server can infer it when it runs inside a workspace already
    linked to a remote project.
    """

    command: list[str]
    project_id: str | None = None
    status_map: dict[str, str] = dataclasses.field(
        default_factory=lambda: dict(DEFAULT_KANBAN_STATUS_MAP)
    )


@dataclasses.dataclass
class LocalBoardConfig:
    """Where a board with no service behind it keeps its cards.

    `dir` is a directory inside the dispatcher container, one JSON document
    per issue. It is the whole configuration: there is no project to name and
    no status_map to correct, because nothing here renames a column — the
    dispatcher's own status strings are what gets stored.
    """

    dir: str


#: The phases the primary will take on as fallback when no worker can. The
#: four roles do not cost the same: a revisor or an auditor reads a diff and
#: writes a verdict, while an implementador writes code across up to three
#: revision rounds and is the phase that blows a handoff budget. Spending the
#: operator's own console on a fresh implementador round is the worst trade
#: available, so it is off by default and an operator who wants it says so.
#: The primary's default ceiling, tightened to the worker threshold when that
#: is stricter. Named rather than inlined because the docstring below argues
#: about it and config.example.yaml quotes it.
DEFAULT_RESERVE_PCT = 60

DEFAULT_FALLBACK_ROLES = ("revisor", "auditor")


#: The permission modes the CLI accepts (`claude --help`, 2.1.280). Checked at
#: load time for the same reason the integer caps are: a typo is otherwise
#: only caught by the CLI itself, which exits on an unknown choice after the
#: quota probe, the lock and the worktree have already been claimed.
PERMISSION_MODES = frozenset(
    {"acceptEdits", "auto", "bypassPermissions", "dontAsk", "manual", "plan"}
)


@dataclasses.dataclass
class Config:
    accounts: list[AccountConfig]
    quota_threshold_pct: int
    quota_cooldown_seconds: int
    heartbeat_ttl_seconds: int
    heartbeat_interval_seconds: int
    projects_root: str
    hive_tasks_dir: str
    state_dir: str
    vibe_kanban: VibeKanbanConfig | None
    local_board: LocalBoardConfig | None
    collector_url: str
    default_model: str
    permission_mode: str | None
    allowed_tools: list[str]
    max_revision_rounds: int
    escalate_effort_after_round: int
    escalated_effort: str
    phase_timeout_seconds: int
    merge_on_done: bool
    mapping_enabled: bool
    mapping_model: str
    mapping_max_turns: int
    gates_enabled: bool
    gates_test_timeout_seconds: int
    #: Which account the conversational thread runs under, or None for a flat
    #: pool. None is the shape every config had before this existed, and it
    #: keeps the picker's old behaviour exactly: no account is ranked last, no
    #: reserve applies and `fallback_roles` never gates anything.
    primary_account: str | None = None
    #: The stricter ceiling the primary is held to. The conversation and any
    #: fallback phase draw on one budget, so a fallback that spends the primary
    #: to the wall does not just stall the queue — it takes the console with
    #: it, and the operator loses the thread that was supposed to decide what
    #: to do about an exhausted pool. Above this line the primary refuses
    #: fallback work out loud and says the pool is dry, which is the answer the
    #: operator actually needs; idleness is recoverable by waiting, a dead
    #: console is not.
    reserve_pct: int = 60
    fallback_roles: list[str] = dataclasses.field(
        default_factory=lambda: list(DEFAULT_FALLBACK_ROLES)
    )


def _load_vibe_kanban(raw: dict) -> VibeKanbanConfig | None:
    """Read the optional `vibe_kanban` block, or None when there is none.

    The board is a visibility aid, not a dependency: a harness with no
    `vibe_kanban` block runs tasks exactly as before and the dispatcher never
    mentions a board it was not given.
    """
    if "vibe_kanban_mcp_url" in raw:
        # This key promised an SSE endpoint that Vibe Kanban's MCP server
        # never served. Failing loudly beats ignoring it, which would leave an
        # operator believing their board is wired up when nothing reaches it.
        raise ValueError(
            "vibe_kanban_mcp_url is gone: Vibe Kanban's MCP server speaks stdio, "
            "not SSE. Replace it with a vibe_kanban block, or drop it to run "
            "without a board."
        )
    block = raw.get("vibe_kanban")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("vibe_kanban must be a mapping")
    command = block.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        raise ValueError("vibe_kanban.command must be a non-empty list of strings")
    project_id = block.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        raise ValueError("vibe_kanban.project_id must be a string")
    status_map = dict(DEFAULT_KANBAN_STATUS_MAP)
    overrides = block.get("status_map") or {}
    if not isinstance(overrides, dict):
        raise ValueError("vibe_kanban.status_map must be a mapping")
    for key, value in overrides.items():
        if key not in DEFAULT_KANBAN_STATUS_MAP:
            raise ValueError(
                f"vibe_kanban.status_map has no status {key!r}; known statuses are "
                + ", ".join(sorted(DEFAULT_KANBAN_STATUS_MAP))
            )
        if not isinstance(value, str) or not value:
            raise ValueError(f"vibe_kanban.status_map[{key!r}] must be a non-empty string")
        status_map[key] = value
    return VibeKanbanConfig(command=command, project_id=project_id, status_map=status_map)


def _load_local_board(raw: dict) -> LocalBoardConfig | None:
    """Read the optional `local_board` block, or None when there is none.

    Opt-in, exactly like `vibe_kanban`: a harness that has been running with
    no cards should not start writing them because it was upgraded.
    """
    block = raw.get("local_board")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("local_board must be a mapping")
    directory = block.get("dir")
    if not isinstance(directory, str) or not directory:
        raise ValueError("local_board.dir must be a non-empty string")
    return LocalBoardConfig(dir=directory)


def _load_boards(raw: dict) -> tuple[VibeKanbanConfig | None, LocalBoardConfig | None]:
    """The one board this config configures, as the pair of optional blocks.

    Both blocks at once is a config error rather than a precedence rule: an
    operator who wrote both meant one of them, and silently ignoring the other
    leaves them watching a board the run never touches.
    """
    vibe_kanban = _load_vibe_kanban(raw)
    local_board = _load_local_board(raw)
    if vibe_kanban is not None and local_board is not None:
        raise ValueError(
            "vibe_kanban and local_board are both configured, and a run talks to one "
            "board. Drop local_board to keep the Vibe Kanban board, or drop "
            "vibe_kanban to keep the local one."
        )
    return vibe_kanban, local_board


def _load_permission_mode(raw: dict) -> str | None:
    """Which permission mode every phase command carries.

    With no mode the CLI runs `-p` under `--permission-prompts host` with no
    host to ask, so everything that would prompt is denied instead — Write and
    Edit inside the phase's own worktree included. Measured end to end on
    2026-09-23: 43 of 62 tool calls denied, not one file written, all three
    phases blocked.

    `acceptEdits` is the default because it is the most permissive mode this
    harness can actually use. `bypassPermissions` is refused at startup when
    the CLI runs as root, which the agent image does; `dontAsk` is precisely
    the denial above; and `auto`, the one listed mode that would also cover
    Bash, is a server-side classifier the CLI can report as unavailable, so it
    is not something to depend on before it has been measured on these
    accounts. Move this to `auto` once it has been.

    `permission_mode: null` drops the flag again, which is how the unflagged
    behaviour above stays measurable.
    """
    mode = raw.get("permission_mode", "acceptEdits")
    if mode is None:
        return None
    if not isinstance(mode, str) or mode not in PERMISSION_MODES:
        raise ValueError(
            f"permission_mode must be null or one of: {', '.join(sorted(PERMISSION_MODES))}"
        )
    return mode


def _load_allowed_tools(raw: dict) -> list[str]:
    """Tool patterns every phase is allowed outright, on top of the mode.

    The mode and this list are two independent grants, measured separately on
    2026-09-23: `acceptEdits` covers the file tools and the CLI's own
    read-only Bash set, but not running a program, and a phase asked to run
    `node --test` was refused six times across three phases — so the task's
    own acceptance criterion was unreachable and nothing in the loop ever
    executed the code. The same prompt with `Bash(node --test*)` allowed ran
    it in two turns. An allowlist with no mode alongside it ran it too, which
    is why this is a separate setting rather than a property of the mode.

    It has to come from here rather than from the phase, because a phase
    cannot grant itself the permission: writing its own
    `.claude/settings.local.json` is refused even under `acceptEdits`, and a
    CLI flag is not on disk for it to reach at all. Narrow entries are the
    point — prefer `Bash(npm test*)` over `Bash(npm*)`, and note that the
    trailing `*` still lets arguments through.

    Empty by default, which sends no flag: what a project's phases may run is
    a decision about that project, and guessing it would be worse than asking.
    """
    tools = raw.get("allowed_tools", [])
    if tools is None:
        return []
    if isinstance(tools, str) or not isinstance(tools, list):
        raise ValueError("allowed_tools must be a list of tool patterns")
    for tool in tools:
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("allowed_tools entries must be non-empty strings")
    return [tool.strip() for tool in tools]


def _load_primary_account(raw: dict, accounts: list[AccountConfig]) -> str | None:
    """Read the optional `primary_account` and mark it on the pool.

    The name has to resolve to an account that exists, the same way an unknown
    `--name` is a usage error for `release-account`: a typo here would
    otherwise load fine and quietly produce a flat pool, which is the failure
    this key exists to prevent.

    Absent, it returns None and nothing is marked. That is deliberate and not
    a missing default: the plan asks for the primary to be named in
    `config.yaml` and not in code, so a config that does not name one gets the
    behaviour it had before the key existed rather than one this function
    guessed.
    """
    name = raw.get("primary_account")
    if name is None:
        return None
    if not isinstance(name, str) or not name.strip():
        raise ValueError("primary_account must be the name of a configured account")
    name = name.strip()
    for account in accounts:
        if account.name == name:
            account.is_primary = True
            return name
    known = ", ".join(a.name for a in accounts) or "none"
    raise ValueError(f"primary_account {name!r} is not a configured account (have: {known})")


def _load_reserve_pct(raw: dict, quota_threshold_pct: int) -> int:
    """Read the optional `reserve_pct`, checked against the worker threshold.

    A reserve above `quota_threshold_pct` would make the primary *more*
    permissive than a worker — the account that is supposed to be spent last
    would be the one still accepting work after the pool has parked — which
    inverts the whole point of the reserve. An explicit value that does that is
    a config error rather than a clamp, because the two numbers together are a
    policy and silently repairing half of it would hide the disagreement.

    The default is the only thing that bends: a config with a worker threshold
    under 60 and no `reserve_pct` never stated a policy to disagree with, and
    failing it over a key it does not use would break a config that loaded
    fine before this one existed.
    """
    reserve_pct = raw.get("reserve_pct", min(DEFAULT_RESERVE_PCT, quota_threshold_pct))
    if isinstance(reserve_pct, bool) or not isinstance(reserve_pct, int) or reserve_pct <= 0:
        raise ValueError("reserve_pct must be a positive integer")
    if reserve_pct > quota_threshold_pct:
        raise ValueError(
            f"reserve_pct ({reserve_pct}) must not exceed quota_threshold_pct "
            f"({quota_threshold_pct}): the primary is held to a stricter ceiling "
            "than a worker, not a looser one"
        )
    return reserve_pct


def _load_fallback_roles(raw: dict) -> list[str]:
    """Read the optional `fallback_roles`, or the cheap two by default.

    Not validated against the role vocabulary on purpose: the roles are the
    dispatcher's own (`dispatcher/learnings.py`), and importing that here to
    check a list of strings would tie config loading to the phase code it
    configures. An unknown name here costs nothing — it simply never matches a
    role the picker is asked about, so the primary stays out of that phase,
    which is the safe direction to be wrong in.
    """
    roles = raw.get("fallback_roles", None)
    if roles is None:
        return list(DEFAULT_FALLBACK_ROLES)
    if isinstance(roles, str) or not isinstance(roles, list):
        raise ValueError("fallback_roles must be a list of role names")
    for role in roles:
        if not isinstance(role, str) or not role.strip():
            raise ValueError("fallback_roles entries must be non-empty strings")
    return [role.strip() for role in roles]


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    accounts = []
    for entry in raw["accounts"]:
        # `AccountConfig(**entry)` would accept `is_primary` here now that the
        # field exists, and two places to name the primary can disagree. One
        # key, at the top level, is the whole design.
        if "is_primary" in entry:
            raise ValueError(
                "accounts entries must not set is_primary: name the primary "
                "once, in the top-level primary_account"
            )
        accounts.append(AccountConfig(**entry))
    primary_account = _load_primary_account(raw, accounts)
    quota_threshold_pct = raw.get("quota_threshold_pct", 90)
    # Checked now that reserve_pct is compared against it: an unvalidated
    # threshold would turn a typo into a TypeError inside _load_reserve_pct
    # rather than a config error with a name on it.
    if (
        isinstance(quota_threshold_pct, bool)
        or not isinstance(quota_threshold_pct, int)
        or quota_threshold_pct <= 0
    ):
        raise ValueError("quota_threshold_pct must be a positive integer")
    reserve_pct = _load_reserve_pct(raw, quota_threshold_pct)
    phase_timeout_seconds = raw.get("phase_timeout_seconds", 7200)
    # A bad value here (0, negative, or the wrong type) previously loaded
    # fine and only surfaced inside exec_claude, after a quota probe, BUSY,
    # the lock and the worktree were already claimed — catch it up front
    # instead. bool is an int subclass in Python, so it needs its own check.
    if isinstance(phase_timeout_seconds, bool) or not isinstance(phase_timeout_seconds, int) or phase_timeout_seconds <= 0:
        raise ValueError("phase_timeout_seconds must be a positive integer")
    mapping_max_turns = raw.get("mapping_max_turns", 40)
    if isinstance(mapping_max_turns, bool) or not isinstance(mapping_max_turns, int) or mapping_max_turns <= 0:
        raise ValueError("mapping_max_turns must be a positive integer")
    quota_cooldown_seconds = raw.get("quota_cooldown_seconds", 1800)
    if (
        isinstance(quota_cooldown_seconds, bool)
        or not isinstance(quota_cooldown_seconds, int)
        or quota_cooldown_seconds <= 0
    ):
        raise ValueError("quota_cooldown_seconds must be a positive integer")
    gates_test_timeout_seconds = raw.get("gates_test_timeout_seconds", 900)
    if (
        isinstance(gates_test_timeout_seconds, bool)
        or not isinstance(gates_test_timeout_seconds, int)
        or gates_test_timeout_seconds <= 0
    ):
        raise ValueError("gates_test_timeout_seconds must be a positive integer")
    vibe_kanban, local_board = _load_boards(raw)
    return Config(
        accounts=accounts,
        quota_threshold_pct=quota_threshold_pct,
        # How long a refusal outranks the /usage numbers. The probe is local
        # and cannot see a refusal at all, so without a floor an account that
        # was just turned away is recovered on healthy local counters and sent
        # straight back to be turned away again. Half an hour is a compromise:
        # long enough to stop that churn, short enough that a limit which
        # lifts early does not cost the pool an account for the day.
        quota_cooldown_seconds=quota_cooldown_seconds,
        heartbeat_ttl_seconds=raw.get("heartbeat_ttl_seconds", 120),
        heartbeat_interval_seconds=raw.get("heartbeat_interval_seconds", 30),
        projects_root=raw["projects_root"],
        hive_tasks_dir=raw["hive_tasks_dir"],
        state_dir=raw["state_dir"],
        vibe_kanban=vibe_kanban,
        local_board=local_board,
        collector_url=raw["collector_url"],
        default_model=raw.get("default_model", "opus"),
        permission_mode=_load_permission_mode(raw),
        allowed_tools=_load_allowed_tools(raw),
        max_revision_rounds=raw.get("max_revision_rounds", 3),
        escalate_effort_after_round=raw.get("escalate_effort_after_round", 2),
        escalated_effort=raw.get("escalated_effort", "high"),
        phase_timeout_seconds=phase_timeout_seconds,
        # Off by default: merging is the one thing a run does to the branch the
        # human works from, so it waits to be asked for. `dispatch merge-task`
        # does the same merge by hand whenever this stays false.
        merge_on_done=bool(raw.get("merge_on_done", False)),
        # Also off by default, and for the same kind of reason: mapping an
        # unmapped project spends quota on a phase the operator did not ask
        # for. Turned on, it runs once per project — before the arquitecto,
        # only when the project has no docs index — and never blocks the task
        # if it fails.
        mapping_enabled=bool(raw.get("mapping_enabled", False)),
        # Cheaper than the roles that decide things: reading a tree and
        # writing down what is there does not need the expensive model.
        mapping_model=raw.get("mapping_model", "sonnet"),
        mapping_max_turns=mapping_max_turns,
        # On by default, unlike the two above: the gates run through
        # `docker exec` with no model in the loop, so they spend no quota, and
        # what they catch — a change with no test, a suite that is already
        # failing — costs a revisor call plus another implementador round when
        # it reaches review instead. They net quota back.
        gates_enabled=bool(raw.get("gates_enabled", True)),
        # The phase timeout is hours long because a phase is a model working;
        # a test suite that has not finished in fifteen minutes is telling the
        # gate something else, and the gate says so and lets review proceed
        # rather than holding the task open for the rest of the afternoon.
        gates_test_timeout_seconds=gates_test_timeout_seconds,
        primary_account=primary_account,
        reserve_pct=reserve_pct,
        fallback_roles=_load_fallback_roles(raw),
    )
