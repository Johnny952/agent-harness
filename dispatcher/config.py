from __future__ import annotations

import dataclasses
import logging
import os
import threading

import yaml

from dispatcher import board_mcp, docker_exec, project_docs, push, role_skills

logger = logging.getLogger(__name__)


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
    #: How many phases this account holds at once (`docs/decisions.md` ADR 84
    #: and 88). 1 is today's behaviour; only the owner raises it, on an account
    #: whose plan allows it. `--parallel` is still the ceiling.
    max_concurrent_phases: int = 1
    #: The account's dind container, the one its agent's `DOCKER_HOST` points
    #: at. Named, never guessed from `container`: a command request of kind
    #: `dind` or `service` resolves to it, and an account without one refuses
    #: those kinds (`docs/decisions.md` ADR 118).
    dind: str | None = None
    #: The models a thread may pick on this account, replacing the top-level
    #: `thread_models` for it (`docs/decisions.md` ADR 169). None is "use that".
    models: list[str] | None = None


#: The config keys the board seam read, all refused now that it is gone
#: (`docs/decisions.md` ADR 59). `vibe_kanban_mcp_url` is the oldest of the
#: three and was already refused; it stays refused, under the one message.
REMOVED_BOARD_KEYS = ("vibe_kanban", "local_board", "vibe_kanban_mcp_url")


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

#: The effort levels the CLI accepts (`claude --help`, 2.1.296), lowest first.
#: Checked at load time like the permission mode, for the same reason.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

#: The roles a phase can run as, which are the keys `role_effort` and
#: `role_model` may name.
PHASE_ROLES = frozenset(role_skills.ROLE_SKILLS) | {project_docs.MAPPER_ROLE}
#: The `role_model` key for the arquitecto's `draft-specs` run (ADR 82):
#: drafting a plan's specs is not designing one task, and may want another
#: model. Not a role: the phase runs as the arquitecto, with its skills and
#: its effort. Unset, the run takes the arquitecto's model.
DRAFT_ROLE = "arquitecto-draft"
#: The role that resolves a merge conflict on a task branch (ADR 277). Not in
#: PHASE_ROLES: it is not a step of the cycle, nothing on the board writes as
#: it, and `run-phase` does not dispatch it; only `resolve-conflict` does. But
#: `role_model` and `role_effort` take it, and unset it runs on the
#: implementador's model, whose code it is merging.
INTEGRADOR_ROLE = "integrador"

EDITABLE = "editable"
READ_ONLY = "read-only"
CREDENTIAL = "credential"


@dataclasses.dataclass(frozen=True)
class ConfigKey:
    key_class: str
    reason: str
    #: The strict check an override of an editable key is held to; None on any
    #: other key. One of bool, positive_int, non_negative_int, string, effort,
    #: list, mapping, nullable_model (see `_check_override_value`).
    kind: str | None = None


def _editable(kind: str, reason: str) -> ConfigKey:
    return ConfigKey(EDITABLE, reason, kind)


def _read_only(reason: str) -> ConfigKey:
    return ConfigKey(READ_ONLY, reason)


def _credential(reason: str) -> ConfigKey:
    return ConfigKey(CREDENTIAL, reason)


_BOARD_REASON = "Read only to be refused (ADR 59); an override naming one is refused as well."

#: Every top-level key `load_config` reads, with its class and the reason for
#: it. `load_config` refuses an overrides key that is not editable, and task 2's
#: `config_writer` imports this table for its settings rows
#: (`docs/decisions.md` ADR 75 part 5, ADR 106). A new key read by
#: `load_config` needs a row here, or the table test fails.
CONFIG_KEYS: dict[str, ConfigKey] = {
    "accounts": _read_only(
        "Names containers. Adding an account is docs/plans/server-deployment.md's, "
        "with a container template."
    ),
    "primary_account": _editable(
        "string",
        "C-3 amended makes the primary a setting a person changes from the console. "
        "It must name a configured account.",
    ),
    "quota_threshold_pct": _editable(
        "positive_int",
        "Quota policy the owner sets. reserve_pct is checked against it on the merged result.",
    ),
    "reserve_pct": _editable(
        "positive_int",
        "Quota policy. It must not exceed the threshold, and the merged check catches that.",
    ),
    "pace_primary_week": _editable("bool", "A boolean policy switch (ADR 48)."),
    "quota_cooldown_seconds": _editable(
        "positive_int", "A timeout that is already validated as a positive integer."
    ),
    "fallback_roles": _editable(
        "list", "Which phases the primary takes. It is a list, replaced whole."
    ),
    "default_model": _editable(
        "string", "Item 0 names it. The override must be a non-empty string."
    ),
    "role_model": _editable(
        "mapping", "Item 0 names it. It is a map, replaced whole, and validated against the roles."
    ),
    "thread_models": _editable(
        "list", "The models a thread may pick (ADR 169). A list, replaced whole, checked on the merge."
    ),
    "mapping_model": _editable("string", "Like default_model, for the cartografo (ADR 66)."),
    "default_effort": _editable("effort", "It is one of EFFORT_LEVELS, checked."),
    "role_effort": _editable(
        "mapping",
        "It is a map, replaced whole, and validated against the roles and the levels.",
    ),
    "escalated_effort": _editable("effort", "It is one of EFFORT_LEVELS, checked."),
    "escalate_effort_after_round": _editable(
        "non_negative_int", "A round count. The override must be a non-negative integer."
    ),
    "escalate_effort_on_repeated_finding": _editable("bool", "A boolean switch (ADR 73)."),
    "escalated_model": _editable(
        "nullable_model",
        "A model ID, or null for off (ADR 432, ADR 441). Reset goes back to config.yaml.",
    ),
    "max_revision_rounds": _editable(
        "positive_int", "It bounds a cycle's spend. The override must be a positive integer."
    ),
    "max_handoff_resumes": _editable(
        "non_negative_int", "The cap C-4 amended puts in the config. It is already validated."
    ),
    "phase_timeout_seconds": _editable("positive_int", "A timeout that is already validated."),
    "gates_test_timeout_seconds": _editable(
        "positive_int", "A timeout that is already validated."
    ),
    "mapping_max_turns": _editable("positive_int", "A cap that is already validated."),
    "merge_on_done": _editable("bool", "Item 0 names it. It is a boolean, and the override must be one."),
    "gates_enabled": _editable("bool", "Item 0 names it. It is a boolean."),
    "mapping_enabled": _editable("bool", "Item 0 names it. It is a boolean."),
    "heartbeat_ttl_seconds": _read_only(
        "Two dispatchers running at once must agree on it, or one reaps the other's "
        "live lock. load_config does not validate it."
    ),
    "heartbeat_interval_seconds": _read_only(
        "It pairs with the TTL above, for the same reason."
    ),
    "projects_root": _read_only("A path inside the containers, fixed by the compose mounts."),
    "hive_tasks_dir": _read_only("A path, the same as above."),
    "state_dir": _read_only("A path, the same as above."),
    "collector_url": _read_only("A service address on the compose network."),
    "board_mcp_url": _read_only(
        "A service address on the compose network, and a grant: it decides whether "
        "the arquitecto's phases get the board's tools (ADR 128)."
    ),
    "permission_mode": _read_only(
        "It widens or narrows what every phase may do. The api's boot-time harness "
        "fingerprint (ADR 41 part 8) reads it."
    ),
    "allowed_tools": _read_only(
        "A standing grant to every phase. docs/plans/command-requests.md is where "
        "grants are given."
    ),
    "mapping_allowed_tools": _read_only(
        "A grant to the cartografo alone, on top of allowed_tools, so the run-skill "
        "generator can run the project's build (ADR 115). A permission is the "
        "operator's to grant by hand."
    ),
    "update_project_before_task": _read_only(
        "Turning it on needs a credential or deploy key on the host that the console "
        "cannot see. That is the second task's ground."
    ),
    "review_conflict_check": _read_only(
        "Turning it on runs the test gate on every task waiting for review after "
        "each merge (ADR 274), a cost in container time the operator decides in "
        "config.yaml."
    ),
    "git_credentials_path": _credential(
        "A path. Renewing the GitHub token at that path is the second task. The key "
        "itself is never edited."
    ),
    "project_keys_dir": _credential(
        "A path to the deploy keys (ADR 56). It is the same as above."
    ),
    "push_vapid_key_path": _credential(
        "A path to the VAPID private key that signs Web Push (ADR 229). The key "
        "itself is never edited."
    ),
    "push_subject": _read_only(
        "The contact the push services see in every push's VAPID claim (ADR 229). "
        "It is read by the board service at its start."
    ),
    **{key: _read_only(_BOARD_REASON) for key in REMOVED_BOARD_KEYS},
    "auto_approve": _read_only(
        "It turns on merges no person looks at (ADR 77). It moves into the project "
        "record of docs/plans/projects.md (ADR 106)."
    ),
    "protected_paths": _read_only(
        "Loosening it widens what merges unattended (ADR 77), and it lives wherever "
        "auto_approve lives (ADR 106)."
    ),
    "push_task_branch": _read_only(
        "It writes to the project's remote with the dispatcher's credential after "
        "every committing phase (ADR 78, ADR 271), and it lives wherever "
        "auto_approve lives (ADR 106)."
    ),
    "project_parallel": _read_only(
        "It sets how many of a project's starts the worker runs at once (ADR 306), "
        "bounded by the pool's phase slots, and it lives wherever auto_approve lives "
        "(ADR 106)."
    ),
}


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
    collector_url: str
    default_model: str
    permission_mode: str | None
    allowed_tools: list[str]
    max_revision_rounds: int
    escalate_effort_after_round: int
    escalated_effort: str
    phase_timeout_seconds: int
    #: Deprecated alias (ADR 240): it merges before the cycle's end moves an
    #: unmerged task to `awaiting_review`. Kept so existing configs load.
    merge_on_done: bool
    mapping_enabled: bool
    mapping_model: str
    mapping_max_turns: int
    gates_enabled: bool
    gates_test_timeout_seconds: int
    #: Whether a task fetches the project's remote and fast-forwards its own
    #: checkout before its first phase runs. Off by default: it needs a
    #: credential mounted into the agent containers that no existing setup has,
    #: and without one every task would open with a warning it cannot act on.
    #: False is the shape every config had before this existed, and it is the
    #: behaviour they all got: the base is whatever the clone was last left at.
    #: Added to allowed_tools for the cartografo's call only (ADR 115).
    mapping_allowed_tools: list[str] = dataclasses.field(default_factory=list)
    update_project_before_task: bool = False
    #: The check at review (ADR 274, `docs/plans/review.md` step 8): the base
    #: merged into a task's branch as it enters review, and into every task
    #: still in review after a merge, then gated; a conflict or a red gate
    #: moves the task to `conflict`. Only tested, never kept. Off: the check
    #: then runs only at merge time, as before.
    review_conflict_check: bool = False
    #: Where that credential is read from, inside the container.
    git_credentials_path: str = docker_exec.GIT_CREDENTIALS_PATH
    #: Where each project's SSH deploy key is, inside the container: one
    #: directory per slug, made by `project-keygen`. A project with a key there
    #: fetches over SSH and the credential above is not consulted.
    project_keys_dir: str = docker_exec.PROJECT_KEYS_DIR
    # Web Push (ADR 229): the VAPID key's path in the board service's
    # container, and the `sub` claim. A missing key turns push off.
    push_vapid_key_path: str = push.VAPID_KEY_PATH
    push_subject: str = push.DEFAULT_SUBJECT
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
    #:
    #: Since `docs/decisions.md` ADR 48 this governs the primary's *session*
    #: window, and is its week's fallback rather than its week's ceiling — see
    #: `pace_primary_week` and `quota.week_ceiling`.
    reserve_pct: int = 60
    fallback_roles: list[str] = dataclasses.field(
        default_factory=lambda: list(DEFAULT_FALLBACK_ROLES)
    )
    #: Pace the primary's week against the reset `/usage` reports, instead of
    #: holding it to the flat `reserve_pct` (ADR 48). On by default. `false`
    #: restores exactly the older behaviour — `reserve_pct` on both of the
    #: primary's windows — and exists because this changes when the operator's
    #: own console accepts work, so disagreeing with the ramp should cost one
    #: line rather than a patch. The ramp's own numbers are constants in
    #: `dispatcher/quota.py`, not keys: see `PACE_START_PCT` and its neighbours.
    pace_primary_week: bool = True
    #: The effort every phase runs at, passed as `--effort` rather than left
    #: to the CLI, so which level ran is in the config and not in whatever the
    #: image's CLI version defaults to (`docs/plans/models-5-5.md`,
    #: *Decisions*, 2). `medium` is Opus 5.5's own default, and the level the
    #: plan starts every role at until a measurement says otherwise.
    default_effort: str = "medium"
    #: A level per role over `default_effort`, such as `{"revisor": "high"}`.
    #: The escalation after `escalate_effort_after_round` still wins over both.
    role_effort: dict[str, str] = dataclasses.field(default_factory=dict)
    #: A model per role over `default_model`, such as
    #: `{"implementador": "claude-sonnet-5-5"}` (`docs/decisions.md` ADR 65).
    #: A model the phase call names itself, the cartografo's `mapping_model`,
    #: still wins over both, and so does `escalated_model` on an escalated
    #: implementador round (ADR 432); the effort escalation alone does not touch it. Also
    #: takes `DRAFT_ROLE`, the arquitecto's `draft-specs` run (ADR 82), and
    #: `INTEGRADOR_ROLE`, which falls back to the implementador's (ADR 277).
    role_model: dict[str, str] = dataclasses.field(default_factory=dict)
    #: How many times a phase that ended without its handoff is resumed with
    #: one "continue" line before it counts as failed. Unattended, a model can
    #: end a turn on a progress report, and under `-p` that ends the phase
    #: (`docs/plans/models-5-5.md`, *Decisions*, 3). 0 fails it at once.
    max_handoff_resumes: int = 2
    #: Escalate the next implementador round as soon as the revisor sends a
    #: round back for a finding it already sent the previous review back for
    #: (`docs/decisions.md` ADR 73), instead of only on the round count. Off
    #: by default, which is exactly the round-count rule alone. On, the
    #: round-count rule still applies: this can only escalate sooner.
    escalate_effort_on_repeated_finding: bool = False
    #: The model an escalated implementador round runs on, under either rule
    #: of `effort_for` (`docs/decisions.md` ADR 432). None, the default, keeps
    #: the role's own model: nothing changes. Other roles never escalate.
    escalated_model: str | None = None
    #: The projects whose finished tasks merge only when every condition of
    #: the automatic approve holds (`docs/plans/autonomy.md` *Decisions*, 1;
    #: `docs/decisions.md` ADR 77), as a map of slug to boolean until the
    #: project record of `projects.md` exists. A project not named, or named
    #: `false`, behaves exactly as before: `merge_on_done` decides. A project
    #: named `true` gets the check even with `merge_on_done` on.
    auto_approve: dict[str, bool] = dataclasses.field(default_factory=dict)
    #: Per project, more repo paths or globs the automatic approve never
    #: merges a change to, over the fixed list in `dispatcher/auto_approve.py`.
    protected_paths: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    #: Per project, whether the dispatcher pushes `agent/task/<id>` to the
    #: project's remote after each phase that left a new commit on it
    #: (`docs/plans/review.md` *Decisions*, 4; ADR 271). A project not named,
    #: or named `false`, never has its task branch pushed, as before.
    push_task_branch: dict[str, bool] = dataclasses.field(default_factory=dict)
    #: Per project, how many `start`s the worker runs at once (ADR 306). A
    #: project not named runs one at a time, as before.
    project_parallel: dict[str, int] = dataclasses.field(default_factory=dict)
    #: Where a phase reaches the board service's MCP endpoint, without the
    #: `?role=` the dispatcher adds per phase (`docs/decisions.md` ADR 128).
    #: None sends no `--mcp-config` and no board tool names to any phase,
    #: which is exactly the behaviour before the service existed.
    board_mcp_url: str | None = board_mcp.DEFAULT_URL
    #: The models a thread may pick, for an account that names none of its own
    #: (`docs/decisions.md` ADR 169). The loader always sets it.
    thread_models: list[str] = dataclasses.field(default_factory=list)
    #: Where this config was loaded from, not config keys: `load_config` sets
    #: both so `refresh_primary` can read the primary override live
    #: (`docs/decisions.md` ADR 176). `overrides_path` None, as on a `Config`
    #: built by hand, keeps the primary as loaded. `config_primary_account` is
    #: `config.yaml`'s own `primary_account`, the fallback for an override
    #: that does not name a configured account; None when it names none.
    #: The path is left out of equality: two loads of the same values are the
    #: same config wherever their files sit.
    overrides_path: str | None = dataclasses.field(default=None, compare=False)
    config_primary_account: str | None = None

    def thread_models_for(self, account_name: str) -> list[str]:
        """The models a thread on `account_name` may pick; `KeyError` if unknown."""
        for account in self.accounts:
            if account.name == account_name:
                return list(account.models if account.models is not None else self.thread_models)
        raise KeyError(account_name)

    def auto_approves(self, slug: str) -> bool:
        """Whether a finished task of `slug` goes through the automatic approve."""
        return self.auto_approve.get(slug, False)

    def pushes_task_branch(self, slug: str) -> bool:
        """Whether `slug`'s task branch is pushed after each committing phase."""
        return self.push_task_branch.get(slug, False)

    def parallel_for(self, slug: str) -> int:
        """How many starts of `slug` the worker may run at once, never above the slots."""
        slots = sum(a.max_concurrent_phases for a in self.accounts)
        return max(1, min(self.project_parallel.get(slug, 1), slots))

    def _escalates(self, round_num: int | None, repeated_finding: bool) -> bool:
        """Whether a round escalates: past `escalate_effort_after_round`, or on
        a repeated finding with `escalate_effort_on_repeated_finding` on."""
        if round_num is not None and round_num > self.escalate_effort_after_round:
            return True
        return repeated_finding and self.escalate_effort_on_repeated_finding

    def effort_for(
        self, role: str, round_num: int | None = None, repeated_finding: bool = False,
    ) -> str:
        """The `--effort` a phase of `role` runs at, in `round_num` if any.

        `repeated_finding` is the cycle saying the last review repeated a
        finding of the one before it; it counts only with
        `escalate_effort_on_repeated_finding` on.
        """
        if self._escalates(round_num, repeated_finding):
            return self.escalated_effort
        return self.role_effort.get(role, self.default_effort)

    def model_for(
        self, role: str, round_num: int | None = None, repeated_finding: bool = False,
    ) -> str:
        """The `--model` a phase of `role` runs on, every call of it included.

        An implementador round that escalates (the same rule as `effort_for`)
        runs on `escalated_model` when it is set (ADR 432). `DRAFT_ROLE` falls
        back to the arquitecto's model before the default, and `INTEGRADOR_ROLE`
        to the implementador's (ADR 277), without a round: they never escalate.
        """
        if (
            self.escalated_model is not None
            and role == "implementador"
            and self._escalates(round_num, repeated_finding)
        ):
            return self.escalated_model
        if role == DRAFT_ROLE and role not in self.role_model:
            return self.model_for("arquitecto")
        if role == INTEGRADOR_ROLE and role not in self.role_model:
            return self.model_for("implementador")
        return self.role_model.get(role, self.default_model)


def _refuse_board_keys(raw: dict) -> None:
    """Fail on a config that still configures a board.

    Refused rather than ignored: a block nothing reads would leave an operator
    watching a board no run moves. On the key being present at all, whatever
    its value — `local_board:` left empty is the same mistake.
    """
    for key in REMOVED_BOARD_KEYS:
        if key in raw:
            raise ValueError(
                f"{key} is no longer a config key: the board seam was removed "
                "(docs/decisions.md ADR 59). The task files are the board and the "
                f"console shows them. Delete {key} from config.yaml."
            )


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


def _check_effort(key: str, level: object) -> str:
    if not isinstance(level, str) or level not in EFFORT_LEVELS:
        raise ValueError(f"{key} must be one of: {', '.join(EFFORT_LEVELS)}")
    return level


def _check_models(key: str, value: object) -> list[str]:
    """A list of model IDs: not a string, not empty, each a non-empty string,
    stripped, none twice. Which IDs exist is the CLI's to say (ADR 169)."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list of model IDs")
    models = []
    for model in value:
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{key} entries must be non-empty model IDs")
        if model.strip() in models:
            raise ValueError(f"{key} names {model.strip()!r} twice")
        models.append(model.strip())
    return models


def _load_max_handoff_resumes(raw: dict) -> int:
    value = raw.get("max_handoff_resumes", 2)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("max_handoff_resumes must be a non-negative integer")
    return value


def _load_role_effort(raw: dict) -> dict[str, str]:
    """`role_effort`, checked: a misspelt role would otherwise run at the
    default without a word, which is the silent change this key exists to
    prevent."""
    block = raw.get("role_effort") or {}
    if not isinstance(block, dict):
        raise ValueError("role_effort must be a mapping of role to effort level")
    accepted = PHASE_ROLES | {INTEGRADOR_ROLE}
    unknown = sorted(set(block) - accepted)
    if unknown:
        raise ValueError(
            f"role_effort names unknown roles {unknown}; "
            f"roles are: {', '.join(sorted(accepted))}"
        )
    return {role: _check_effort(f"role_effort.{role}", level) for role, level in block.items()}


def _load_role_model(raw: dict) -> dict[str, str]:
    """`role_model`, checked like `role_effort` and for the same reason: a
    misspelt role would run on `default_model` without a word. The model ID
    itself is only checked for being one; which IDs exist is the CLI's to say."""
    block = raw.get("role_model") or {}
    if not isinstance(block, dict):
        raise ValueError("role_model must be a mapping of role to model ID")
    if project_docs.MAPPER_ROLE in block:
        raise ValueError(
            f"role_model names {project_docs.MAPPER_ROLE}, but its model is set "
            f"by mapping_model, which wins over role_model; remove it from "
            f"role_model and set mapping_model instead"
        )
    accepted = (PHASE_ROLES - {project_docs.MAPPER_ROLE}) | {DRAFT_ROLE, INTEGRADOR_ROLE}
    unknown = sorted(set(block) - accepted)
    if unknown:
        raise ValueError(
            f"role_model names unknown roles {unknown}; "
            f"roles are: {', '.join(sorted(accepted))}"
        )
    for role, model in block.items():
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"role_model.{role} must be a non-empty model ID")
    return dict(block)


def _load_escalated_model(raw: dict) -> str | None:
    """`escalated_model`: None when absent or null, else a non-empty model ID
    (ADR 432), checked the way `default_model` and `role_model` are."""
    value = raw.get("escalated_model")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("escalated_model must be a non-empty model ID or null")
    return value


def _load_allowed_tools(raw: dict, key: str = "allowed_tools") -> list[str]:
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

    `mapping_allowed_tools` is read through here too, under its own `key`: the
    same shape, granted to the cartografo's call alone (ADR 115).
    """
    tools = raw.get(key, [])
    if tools is None:
        return []
    if isinstance(tools, str) or not isinstance(tools, list):
        raise ValueError(f"{key} must be a list of tool patterns")
    for tool in tools:
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"{key} entries must be non-empty strings")
    return [tool.strip() for tool in tools]


def _load_board_mcp_url(raw: dict) -> str | None:
    """Read the optional `board_mcp_url`: absent is the default, null is off.

    Absent gives `board_mcp.DEFAULT_URL`, the service's name on ia_harness_net,
    so a stack that runs the service needs no line for it. `null` or an empty
    string turns it off, for a stack that does not run the service: a phase
    told about a server that is not there spends its first turns finding out.

    The URL must carry no query or fragment of its own. The dispatcher appends
    `?role=<role>`, and the service answers 400 to any parameter but `role`,
    so a query here would fail every call rather than load.
    """
    url = raw.get("board_mcp_url", board_mcp.DEFAULT_URL)
    if url is None:
        return None
    if not isinstance(url, str):
        raise ValueError("board_mcp_url must be a URL, or null to turn it off")
    url = url.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"board_mcp_url {url!r} must be an http:// or https:// URL")
    if "?" in url or "#" in url:
        raise ValueError(
            f"board_mcp_url {url!r} must have no query or fragment: the dispatcher "
            "adds ?role= itself"
        )
    return url


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

def _load_auto_approve(raw: dict) -> dict[str, bool]:
    """`auto_approve`, a map of project slug to boolean (ADR 77).

    Strictly booleans: `"no"` or `0` read through `bool()` would turn the
    one key that lets a merge happen unattended on by a typo.
    """
    block = raw.get("auto_approve") or {}
    if not isinstance(block, dict):
        raise ValueError("auto_approve must be a mapping of project slug to true or false")
    for slug, value in block.items():
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("auto_approve keys must be project slugs")
        if not isinstance(value, bool):
            raise ValueError(f"auto_approve.{slug} must be true or false")
    return dict(block)


def _load_push_task_branch(raw: dict) -> dict[str, bool]:
    """`push_task_branch`, a map of project slug to boolean (ADR 271).

    Strictly booleans, like `auto_approve`: a typo must not start writing to
    a remote.
    """
    block = raw.get("push_task_branch") or {}
    if not isinstance(block, dict):
        raise ValueError("push_task_branch must be a mapping of project slug to true or false")
    for slug, value in block.items():
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("push_task_branch keys must be project slugs")
        if not isinstance(value, bool):
            raise ValueError(f"push_task_branch.{slug} must be true or false")
    return dict(block)


def _load_project_parallel(raw: dict, slots: int) -> dict[str, int]:
    """`project_parallel`, a map of project slug to a start ceiling (ADR 306).

    At most the pool's phase slots, as `queue --parallel` is checked in `cli.py`.
    """
    block = raw.get("project_parallel") or {}
    if not isinstance(block, dict):
        raise ValueError("project_parallel must be a mapping of project slug to a number")
    for slug, value in block.items():
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("project_parallel keys must be project slugs")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"project_parallel.{slug} must be a whole number of at least 1")
        if value > slots:
            raise ValueError(
                f"project_parallel.{slug}: at most {slots}, the pool's phase slots"
            )
    return dict(block)


def _load_protected_paths(raw: dict) -> dict[str, list[str]]:
    """`protected_paths`, a map of project slug to a list of repo paths or globs."""
    block = raw.get("protected_paths") or {}
    if not isinstance(block, dict):
        raise ValueError("protected_paths must be a mapping of project slug to a list of paths")
    loaded: dict[str, list[str]] = {}
    for slug, paths in block.items():
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("protected_paths keys must be project slugs")
        if not isinstance(paths, list):
            raise ValueError(f"protected_paths.{slug} must be a list of paths")
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"protected_paths.{slug} entries must be non-empty paths")
        loaded[slug] = [path.strip() for path in paths]
    return loaded


def _check_override_value(key: str, value: object, kind: str) -> str | None:
    """The strict check an override is held to (ADR 75 part 1), or the reason
    it fails. `config.yaml` keeps its coercions; only an override is strict."""
    if kind == "bool":
        return None if isinstance(value, bool) else "must be true or false"
    if kind in ("positive_int", "non_negative_int"):
        floor = 1 if kind == "positive_int" else 0
        if isinstance(value, bool) or not isinstance(value, int) or value < floor:
            return "must be a positive integer" if floor else "must be a non-negative integer"
        return None
    if kind == "string":
        ok = isinstance(value, str) and value.strip()
        return None if ok else "must be a non-empty string"
    if kind == "effort":
        ok = isinstance(value, str) and value in EFFORT_LEVELS
        return None if ok else f"must be one of: {', '.join(EFFORT_LEVELS)}"
    if kind == "list":
        return None if isinstance(value, list) else "must be a list"
    if kind == "mapping":
        return None if isinstance(value, dict) else "must be a mapping"
    if kind == "nullable_model":
        ok = value is None or (isinstance(value, str) and value.strip())
        return None if ok else "must be a non-empty model ID or null"
    raise ValueError(f"{key}: unknown kind {kind!r}")


def _overrides_error(overrides_path: str, keys: str, detail: str, plural: bool = False) -> ValueError:
    """The one message every error the overrides cause goes through."""
    it = "them" if plural else "it"
    return ValueError(
        f"{overrides_path}: {detail}. Reset {keys} from the console or "
        f"delete {it} from {overrides_path}."
    )


def _read_overrides(overrides_path: str) -> dict:
    """The overrides file as a mapping, each key checked against `CONFIG_KEYS`
    and its kind (`docs/decisions.md` ADR 75 part 1, ADR 106)."""
    try:
        with open(overrides_path) as f:
            loaded = yaml.safe_load(f)
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as err:
        raise _overrides_error(
            overrides_path, "the overrides", f"does not parse ({err})"
        ) from err
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise _overrides_error(
            overrides_path, "the overrides", "must be a mapping of config key to value"
        )
    for key in sorted(loaded, key=str):
        value = loaded[key]
        spec = CONFIG_KEYS.get(key) if isinstance(key, str) else None
        if spec is None:
            detail = f"{key!r} is not a config key"
        elif spec.key_class != EDITABLE:
            detail = f"{key} is {spec.key_class} and cannot be overridden: {spec.reason}"
        elif value is None and spec.kind != "nullable_model":
            detail = f"{key} is null; null is not a value, a reset is deleting the key"
        else:
            problem = _check_override_value(key, value, spec.kind)
            if problem is None:
                continue
            detail = f"{key} {problem}"
        raise _overrides_error(overrides_path, str(key), detail)
    return loaded


def load_config(path: str, *, overrides_path: str | None = None) -> Config:
    """`config.yaml` with `config.d/overrides.yaml` merged over it, each
    top-level key replaced whole (`docs/decisions.md` ADR 75, ADR 106)."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    if overrides_path is None:
        overrides_path = os.path.join(os.path.dirname(path), "config.d", "overrides.yaml")
    overrides = _read_overrides(overrides_path)
    cfg = _build_merged(raw, overrides, overrides_path) if overrides else _build_config(raw)
    cfg.overrides_path = overrides_path
    base = raw.get("primary_account")
    base = base.strip() if isinstance(base, str) else None
    cfg.config_primary_account = base if any(a.name == base for a in cfg.accounts) else None
    return cfg


def _build_merged(raw: dict, overrides: dict, overrides_path: str) -> Config:
    try:
        return _build_config({**raw, **overrides})
    except ValueError as err:
        # Only the overrides' fault if config.yaml alone loads (ADR 106 part 3);
        # otherwise its own error propagates unchanged.
        _build_config(raw)
        keys = sorted(overrides)
        raise _overrides_error(
            overrides_path, ", ".join(keys), str(err), plural=len(keys) > 1
        ) from err


#: One refresh at a time: the queue runs cycles on threads that share one
#: `Config`, and two refreshes interleaving their `is_primary` writes could
#: leave two accounts marked.
_primary_lock = threading.Lock()
#: The last warning `refresh_primary` logged, so a bad override is said once
#: per change of problem rather than on every pick of a recheck loop.
_last_primary_warning: str | None = None


def _override_primary(cfg: Config) -> tuple[str | None, str | None]:
    """The primary the overrides file names now, else `config.yaml`'s, and
    the warning to log when the override is not usable (ADR 176)."""
    base = cfg.config_primary_account
    try:
        with open(cfg.overrides_path) as f:
            loaded = yaml.safe_load(f)
    except FileNotFoundError:
        return base, None
    except (OSError, yaml.YAMLError) as err:
        return base, (
            f"{cfg.overrides_path} cannot be read ({err}); the primary account "
            f"is config.yaml's ({base or 'none'})"
        )
    if not isinstance(loaded, dict) or "primary_account" not in loaded:
        return base, None
    name = loaded["primary_account"]
    if isinstance(name, str) and any(a.name == name.strip() for a in cfg.accounts):
        return name.strip(), None
    known = ", ".join(a.name for a in cfg.accounts) or "none"
    return base, (
        f"{cfg.overrides_path}: primary_account {name!r} is not a configured account "
        f"(have: {known}); the primary account is config.yaml's ({base or 'none'})"
    )


def refresh_primary(cfg: Config) -> str | None:
    """Re-read the primary override and re-mark the pool, in place.

    The console changes the primary through the config writer's
    `config.d/overrides.yaml` (ADR 75), and a change applies to the next pick
    without a restart (`docs/plans/main-thread.md` *Decisions*, 3; ADR 176).
    Only `primary_account` is read live; every other override still applies
    when the dispatcher starts. An override that names no configured account
    falls back to `config.yaml`'s primary with a warning instead of failing a
    pick. A phase already running keeps its account: this only moves the mark
    the next pick and its quota gate read.
    """
    global _last_primary_warning
    if cfg.overrides_path is None:
        return cfg.primary_account
    name, warning = _override_primary(cfg)
    with _primary_lock:
        if warning is not None and warning != _last_primary_warning:
            logger.warning("%s", warning)
        _last_primary_warning = warning
        if name != cfg.primary_account:
            logger.warning(
                "the primary account is now %s (was %s)",
                name or "none", cfg.primary_account or "none",
            )
            for account in cfg.accounts:
                account.is_primary = account.name == name
            cfg.primary_account = name
    return name


def _build_config(raw: dict) -> Config:
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
        account = AccountConfig(**entry)
        slots = account.max_concurrent_phases
        if isinstance(slots, bool) or not isinstance(slots, int) or slots < 1:
            raise ValueError(
                f"account {account.name}: max_concurrent_phases must be an "
                f"integer of at least 1, not {slots!r}"
            )
        dind = account.dind
        if dind is not None and (not isinstance(dind, str) or not dind.strip()):
            raise ValueError(
                f"account {account.name}: dind must be a non-empty string, "
                f"the account's dind container, not {dind!r}"
            )
        if account.models is not None:
            account.models = _check_models(f"account {account.name}: models", account.models)
        accounts.append(account)
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
    _refuse_board_keys(raw)
    default_model = raw.get("default_model", "claude-opus-5-5")
    thread_models = raw.get("thread_models")
    thread_models = [default_model] if thread_models is None else _check_models(
        "thread_models", thread_models
    )
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
        collector_url=raw["collector_url"],
        default_model=default_model,
        thread_models=thread_models,
        permission_mode=_load_permission_mode(raw),
        allowed_tools=_load_allowed_tools(raw),
        mapping_allowed_tools=_load_allowed_tools(raw, "mapping_allowed_tools"),
        max_revision_rounds=raw.get("max_revision_rounds", 3),
        escalate_effort_after_round=raw.get("escalate_effort_after_round", 2),
        escalated_effort=_check_effort("escalated_effort", raw.get("escalated_effort", "high")),
        escalate_effort_on_repeated_finding=bool(
            raw.get("escalate_effort_on_repeated_finding", False)
        ),
        default_effort=_check_effort("default_effort", raw.get("default_effort", "medium")),
        role_effort=_load_role_effort(raw),
        role_model=_load_role_model(raw),
        escalated_model=_load_escalated_model(raw),
        max_handoff_resumes=_load_max_handoff_resumes(raw),
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
        mapping_model=raw.get("mapping_model", "claude-sonnet-5-5"),
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
        # Off by default, like merge_on_done and for a neighbouring reason:
        # this one reaches the network and needs a credential the operator has
        # to put on the host first. Turned on, it runs once per task, before
        # the first phase, and only ever fast-forwards — see
        # docker_exec.update_project_branch for what each outcome means.
        update_project_before_task=bool(raw.get("update_project_before_task", False)),
        review_conflict_check=bool(raw.get("review_conflict_check", False)),
        git_credentials_path=raw.get(
            "git_credentials_path", docker_exec.GIT_CREDENTIALS_PATH
        ),
        project_keys_dir=raw.get("project_keys_dir", docker_exec.PROJECT_KEYS_DIR),
        push_vapid_key_path=raw.get("push_vapid_key_path", push.VAPID_KEY_PATH),
        push_subject=raw.get("push_subject", push.DEFAULT_SUBJECT),
        primary_account=primary_account,
        reserve_pct=reserve_pct,
        fallback_roles=_load_fallback_roles(raw),
        # On by default, and no loader of its own: unlike reserve_pct there is
        # no other key to check it against, so bool() beside gates_enabled is
        # the whole reading.
        pace_primary_week=bool(raw.get("pace_primary_week", True)),
        auto_approve=_load_auto_approve(raw),
        protected_paths=_load_protected_paths(raw),
        push_task_branch=_load_push_task_branch(raw),
        project_parallel=_load_project_parallel(
            raw, sum(a.max_concurrent_phases for a in accounts)
        ),
        board_mcp_url=_load_board_mcp_url(raw),
    )
