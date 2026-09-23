import subprocess

import pytest

from dispatcher import gates, project_docs

INDEX_WITH_TESTS = "---\nbuild: npm run build\ntest: npm test\n---\n\n# myproj\n"
INDEX_WITHOUT_TESTS = "---\nbuild: npm run build\n---\n\n# myproj\n"


class _Worktree:
    """The container side of the gates, scripted: one answer per command shape.

    Every gate reaches the worktree through `run_docker_exec`, so that one
    function is the whole seam. Answering by command shape rather than by call
    order is what lets a test about one gate stay about one gate — the other
    three still run, against defaults that say nothing.
    """

    def __init__(
        self,
        *,
        branch: str | None = "main",
        merge_base: str | None = "base-sha",
        diff: tuple[str, ...] = (),
        untracked: tuple[str, ...] = (),
        index: str = INDEX_WITHOUT_TESTS,
        test: tuple[int, str] | None = None,
        timeout: bool = False,
        pointers: tuple[str, ...] = (),
        present: tuple[str, ...] = (),
    ) -> None:
        self.branch = branch
        self.merge_base = merge_base
        self.diff = diff
        self.untracked = untracked
        self.index = index
        self.test = test
        self.timeout = timeout
        self.pointers = pointers
        self.present = set(present)
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []

    def install(self, monkeypatch) -> "_Worktree":
        monkeypatch.setattr(gates.docker_exec, "run_docker_exec", self)
        monkeypatch.setattr(
            gates.docker_exec, "current_branch", lambda container, project_dir: self.branch
        )
        return self

    def ran(self, prefix: list[str]) -> bool:
        return any(command[: len(prefix)] == prefix for command in self.commands)

    def __call__(self, container, workdir, command, env=None, timeout=None):
        self.commands.append(list(command))
        self.envs.append(env)

        if command[:2] == ["git", "merge-base"]:
            if self.merge_base is None:
                return _done(command, 1, stderr="fatal: Not a valid object name\n")
            return _done(command, stdout=f"{self.merge_base}\n")
        if command[:2] == ["git", "diff"]:
            return _done(command, stdout="".join(f"{p}\n" for p in self.diff))
        if command[:2] == ["git", "ls-files"]:
            return _done(command, stdout="".join(f"{p}\n" for p in self.untracked))
        if command == ["cat", project_docs.INDEX]:
            return _done(command, stdout=self.index)
        if command[:2] == ["sh", "-c"]:
            if self.timeout:
                raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
            returncode, output = self.test or (0, "")
            return _done(command, returncode, stdout=output)
        if command[0] == "grep":
            if not self.pointers:
                return _done(command, 1)
            return _done(command, stdout="".join(f"{line}\n" for line in self.pointers))
        if command[:3] == ["ls", "-d", "--"]:
            missing = [p for p in command[3:] if p not in self.present]
            stderr = "".join(
                f"ls: cannot access '{p}': No such file or directory\n" for p in missing
            )
            return _done(command, 2 if missing else 0, stderr=stderr)
        raise AssertionError(f"the gates ran a command no fake answers: {command}")


def _done(command, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _run(worktree: _Worktree, **kwargs) -> gates.Report:
    return gates.run(
        "agent-cuenta1",
        "/data/projects/myproj/worktrees/task-7",
        "/data/projects/myproj",
        test_timeout_seconds=kwargs.pop("test_timeout_seconds", 900),
        **kwargs,
    )


def _findings(report: gates.Report, gate: str) -> list[gates.Finding]:
    return [f for f in report.findings if f.gate == gate]


# --------------------------------------------------------------------------
# What a path looks like
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "tests/dispatcher/test_gates.py",
    "src/__tests__/button.tsx",
    "spec/models/user_spec.rb",
    "dispatcher/gates_test.go",
    "conftest.py",
    "e2e/checkout.spec.ts",
])
def test_anything_test_shaped_counts_as_a_test(path: str) -> None:
    """The generosity is deliberate and one-directional: a test this misses
    becomes a wrong accusation and a wasted round, while one it over-counts
    only hands the judgement back to the revisor."""
    assert gates.is_test(path) is True


@pytest.mark.parametrize("path", ["dispatcher/gates.py", "src/button.tsx", "docs/README.md"])
def test_ordinary_source_is_not_a_test(path: str) -> None:
    assert gates.is_test(path) is False


@pytest.mark.parametrize("path", ["dispatcher/gates.py", "src/button.tsx", "scripts/deploy.sh"])
def test_source_files_are_code(path: str) -> None:
    assert gates.is_code(path) is True


@pytest.mark.parametrize("path", ["README.md", "package.json", "docs/tools/mapper.py"])
def test_docs_and_data_are_not_code(path: str) -> None:
    """`docs/` is excluded by path, not by suffix: the mapper writes scripts
    and snippets in there, and asking for a test on a documented example is
    the kind of false finding that teaches a role to ignore the gates."""
    assert gates.is_code(path) is False


@pytest.mark.parametrize("path", [
    "api/openapi.yaml",
    "api/swagger.json",
    "db/migrations/001_init.sql",
    ".env.example",
    "prisma/schema.prisma",
    "proto/user.proto",
    "graph/user.graphql",
])
def test_a_contract_is_anything_another_program_depends_on_by_name(path: str) -> None:
    assert gates.is_contract(path) is True


@pytest.mark.parametrize("path", ["dispatcher/gates.py", "docs/README.md", "src/button.tsx"])
def test_ordinary_files_are_not_contracts(path: str) -> None:
    assert gates.is_contract(path) is False


@pytest.mark.parametrize("raw,expected", [
    ("`docs/decisions.md`", "docs/decisions.md"),
    ("`docs/decisions.md#adr-4`", "docs/decisions.md"),
    ("](./docs/architecture.md)", "docs/architecture.md"),
    ("](docs/implementations/task-7.md)", "docs/implementations/task-7.md"),
])
def test_a_citation_yields_the_path_it_points_at(raw: str, expected: str) -> None:
    assert gates.pointer_token(raw) == expected


@pytest.mark.parametrize("raw", [
    "`npm test -- --run`",     # prose and commands live in backticks too
    "`README.md`",             # no slash: a bare name is not a pointer
    "`dispatcher/gates`",      # no extension: a module, not a file
    "](https://example.com/a.html)",
    "`src/**/*.py`",           # a glob names no single file
    "`--resume`",
    "`/etc/hosts`",            # absolute: not this repo's to check
    "``",
])
def test_everything_that_is_not_a_path_is_rejected(raw: str) -> None:
    """Rejecting too much is the safe side: a false broken pointer sends the
    auditor after a file that was never meant to exist."""
    assert gates.pointer_token(raw) is None


def test_a_pointer_can_resolve_from_the_root_or_from_beside_the_doc() -> None:
    """Both spellings are asked about, and the one that is nonsense — a
    root-relative path read as relative to the doc — is simply a path that does
    not exist, which costs nothing: the gate reports a pointer only when every
    candidate is missing."""
    assert gates._candidates("docs/architecture.md", "decisions.md") == [
        "decisions.md",
        "docs/decisions.md",
    ]
    assert "docs/decisions.md" in gates._candidates("docs/architecture.md", "docs/decisions.md")


def test_a_pointer_that_climbs_out_of_the_worktree_is_not_asked_about() -> None:
    """`ls` would be asked about a path outside the checkout, and whatever it
    answered would say nothing about this repo."""
    assert gates._candidates("docs/README.md", "../secrets.yaml") == ["secrets.yaml"]


def test_a_sample_says_how_many_it_left_out() -> None:
    assert gates._sample(["a", "b"]) == "`a`, `b`"
    assert gates._sample([str(n) for n in range(7)]).endswith("(+2 more)")


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

def test_an_empty_report_renders_to_nothing() -> None:
    """The gate section is appended to the handoff body, so a report with no
    findings has to render empty rather than to a heading with nothing in it."""
    assert gates.Report().render() == ""
    assert gates.Report().blocking is False
    assert gates.Report().needs_answer is False


def test_only_failing_tests_block_but_a_question_still_earns_a_resume() -> None:
    ask = gates.Report([gates.Finding(gates.TESTS_IN_DIFF, "no test", gates.ASK)])
    note = gates.Report([gates.Finding(gates.POINTERS, "gone", gates.NOTE)])
    blocked = gates.Report([gates.Finding(gates.TESTS_RUN, "failed", gates.BLOCKING)])

    assert (ask.blocking, ask.needs_answer) == (False, True)
    assert (note.blocking, note.needs_answer) == (False, False)
    assert (blocked.blocking, blocked.needs_answer) == (True, True)


def test_the_rendered_section_names_the_gate_and_the_level() -> None:
    report = gates.Report([gates.Finding(gates.TESTS_RUN, "npm test failed", gates.BLOCKING)])
    rendered = report.render()

    assert rendered.startswith("**Dispatcher gates**")
    assert "`tests-run` (blocking) — npm test failed" in rendered


def test_the_resume_prompt_carries_only_what_needs_an_answer() -> None:
    """A NOTE rides along in the task file for the revisor. Repeating it in the
    resume would spend the phase's one retry on something nobody asked it to
    fix."""
    report = gates.Report([
        gates.Finding(gates.TESTS_IN_DIFF, "code changed and no test did", gates.ASK),
        gates.Finding(gates.POINTERS, "a pointer is broken", gates.NOTE),
    ])
    prompt = report.resume_prompt()

    assert "code changed and no test did" in prompt
    assert "a pointer is broken" not in prompt
    assert "risks" in prompt


# --------------------------------------------------------------------------
# What changed
# --------------------------------------------------------------------------

def test_the_diff_gates_are_skipped_when_there_is_no_fork_point(monkeypatch) -> None:
    """Never fall back to HEAD: against HEAD the diff holds only this round's
    uncommitted edits, so a round 2 that fixes one line looks like code with no
    test even when the test landed in round 1."""
    worktree = _Worktree(branch=None, diff=("dispatcher/gates.py",)).install(monkeypatch)

    report = _run(worktree)

    assert report.findings == []
    assert not worktree.ran(["git", "diff"])


def test_an_unknown_merge_base_skips_them_too(monkeypatch) -> None:
    worktree = _Worktree(merge_base=None, diff=("dispatcher/gates.py",)).install(monkeypatch)

    assert _run(worktree).findings == []
    assert not worktree.ran(["git", "diff"])


def test_changed_paths_unions_the_diff_with_the_files_nobody_added_yet(monkeypatch) -> None:
    """A brand new test is untracked, which is exactly the file the first gate
    looks for; a diff alone would miss it and accuse the phase of skipping it."""
    worktree = _Worktree(
        diff=("dispatcher/gates.py", "docs/README.md"),
        untracked=("tests/dispatcher/test_gates.py", "docs/README.md"),
    ).install(monkeypatch)

    assert gates.changed_paths("agent-cuenta1", "/w", "base-sha") == [
        "dispatcher/gates.py",
        "docs/README.md",
        "tests/dispatcher/test_gates.py",
    ]


def test_a_failed_git_command_costs_its_half_of_the_diff_not_the_gate(monkeypatch) -> None:
    def fake_run_docker_exec(container, workdir, command, env=None, timeout=None):
        if command[:2] == ["git", "diff"]:
            return _done(command, 128, stderr="fatal: bad revision\n")
        return _done(command, stdout="tests/test_new.py\n")

    monkeypatch.setattr(gates.docker_exec, "run_docker_exec", fake_run_docker_exec)

    assert gates.changed_paths("agent-cuenta1", "/w", "base-sha") == ["tests/test_new.py"]


# --------------------------------------------------------------------------
# Gate 1 — a change to code is a change to a test
# --------------------------------------------------------------------------

def test_code_with_no_test_is_asked_about(monkeypatch) -> None:
    worktree = _Worktree(diff=("dispatcher/gates.py", "docs/README.md")).install(monkeypatch)

    finding, = _findings(_run(worktree), gates.TESTS_IN_DIFF)

    assert finding.level == gates.ASK
    assert "`dispatcher/gates.py`" in finding.detail


def test_code_that_arrives_with_a_test_is_not(monkeypatch) -> None:
    worktree = _Worktree(
        diff=("dispatcher/gates.py",), untracked=("tests/dispatcher/test_gates.py",),
    ).install(monkeypatch)

    assert _findings(_run(worktree), gates.TESTS_IN_DIFF) == []


def test_a_change_that_touches_no_code_is_not_asked_about(monkeypatch) -> None:
    """Docs and config are changes too, and a task that only edits them has
    nothing to test."""
    worktree = _Worktree(diff=("docs/README.md", "config.example.yaml")).install(monkeypatch)

    assert _findings(_run(worktree), gates.TESTS_IN_DIFF) == []


# --------------------------------------------------------------------------
# Gate 2 — the project's own tests pass
# --------------------------------------------------------------------------

def test_a_project_with_no_test_command_gets_no_test_gate(monkeypatch) -> None:
    """An unmapped project has nothing to run, and inventing a command would be
    worse than running none."""
    worktree = _Worktree(index=INDEX_WITHOUT_TESTS).install(monkeypatch)

    assert _run(worktree).findings == []
    assert not worktree.ran(["sh", "-c"])


def test_a_passing_suite_says_nothing(monkeypatch) -> None:
    worktree = _Worktree(index=INDEX_WITH_TESTS, test=(0, "42 passed\n")).install(monkeypatch)

    assert _run(worktree).findings == []
    assert worktree.ran(["sh", "-c", "npm test"])


def test_a_failing_suite_blocks_the_round_and_parks_its_output(monkeypatch, tmp_path) -> None:
    """The output cannot ride in the handoff — it is far over the byte budget —
    and it cannot live in the repo, because a reviewing checkout is rebuilt
    every round. It goes to the task's scratch dir, which both sides mount at
    the same path, so the finding can cite it and the next round can open it."""
    worktree = _Worktree(
        index=INDEX_WITH_TESTS, test=(1, "FAIL src/button.test.tsx\n1 failed\n"),
    ).install(monkeypatch)
    log_path = str(tmp_path / "scratch" / "gates-round-1.log")

    finding, = _findings(_run(worktree, test_log_path=log_path), gates.TESTS_RUN)

    assert finding.level == gates.BLOCKING
    assert log_path in finding.detail
    written = (tmp_path / "scratch" / "gates-round-1.log").read_text()
    assert written.startswith("$ npm test")
    assert "FAIL src/button.test.tsx" in written


def test_a_failing_suite_with_nowhere_to_park_carries_its_own_tail(monkeypatch) -> None:
    worktree = _Worktree(index=INDEX_WITH_TESTS, test=(1, "AssertionError: 1 != 2\n")).install(
        monkeypatch
    )

    finding, = _findings(_run(worktree), gates.TESTS_RUN)

    assert finding.level == gates.BLOCKING
    assert "AssertionError: 1 != 2" in finding.detail


@pytest.mark.parametrize("failure", [
    (127, "sh: 1: npm: not found\n"),
    (1, "Error: Cannot find module 'vitest'\n"),
    (1, "ModuleNotFoundError: No module named 'pytest'\n"),
    (1, "npm ERR! Missing script: \"test\"\n"),
])
def test_a_suite_that_never_ran_is_a_note_not_a_block(monkeypatch, failure) -> None:
    """A fresh worktree has no `node_modules` and no virtualenv. Blocking on
    that would burn real quota on something no implementador can fix from
    inside its session, so it is reported and review proceeds."""
    worktree = _Worktree(index=INDEX_WITH_TESTS, test=failure).install(monkeypatch)

    finding, = _findings(_run(worktree), gates.TESTS_RUN)

    assert finding.level == gates.NOTE
    assert "missing dependencies" in finding.detail


def test_a_suite_that_does_not_finish_is_a_note_too(monkeypatch) -> None:
    """Three more rounds of implementador quota is a steep price for a suite
    that is merely slow. The revisor is told, and the timeout is the thing to
    move if this keeps happening."""
    worktree = _Worktree(index=INDEX_WITH_TESTS, timeout=True).install(monkeypatch)

    finding, = _findings(_run(worktree, test_timeout_seconds=30), gates.TESTS_RUN)

    assert finding.level == gates.NOTE
    assert "30s" in finding.detail


def test_the_project_command_runs_the_way_the_project_expects(monkeypatch) -> None:
    """Every command whose output the gates parse gets the C locale. The
    project's own test command is not one of them — it is run as the project
    runs it, in whatever environment the image gives it."""
    worktree = _Worktree(index=INDEX_WITH_TESTS, test=(0, "")).install(monkeypatch)

    _run(worktree)

    env, = [
        env for command, env in zip(worktree.commands, worktree.envs) if command[:2] == ["sh", "-c"]
    ]
    assert env is None


# --------------------------------------------------------------------------
# Gate 3 — a contract that moves takes its documentation with it
# --------------------------------------------------------------------------

def test_a_contract_with_no_documentation_rides_along_as_a_note(monkeypatch) -> None:
    """A NOTE, not an ASK: whether the change needs writing down is a judgement
    about what the contract means, which is the revisor's call, not a shell's."""
    worktree = _Worktree(diff=("api/openapi.yaml", "tests/api_test.py")).install(monkeypatch)

    finding, = _findings(_run(worktree), gates.CONTRACT_DOCS)

    assert finding.level == gates.NOTE
    assert "`api/openapi.yaml`" in finding.detail


def test_a_contract_that_arrives_with_a_doc_says_nothing(monkeypatch) -> None:
    worktree = _Worktree(diff=("api/openapi.yaml", "docs/architecture.md")).install(monkeypatch)

    assert _findings(_run(worktree), gates.CONTRACT_DOCS) == []


# --------------------------------------------------------------------------
# Gate 4 — the pointers in docs/ still land somewhere
# --------------------------------------------------------------------------

def test_a_pointer_whose_target_is_gone_is_reported_with_the_doc_that_cites_it(
    monkeypatch,
) -> None:
    worktree = _Worktree(
        pointers=(
            "docs/README.md:`docs/decisions.md`",
            "docs/README.md:`docs/architecture.md`",
        ),
        present=("docs/decisions.md",),
    ).install(monkeypatch)

    finding, = _findings(_run(worktree), gates.POINTERS)

    assert finding.level == gates.NOTE
    assert "`docs/README.md` points at `docs/architecture.md`" in finding.detail
    assert "decisions" not in finding.detail


def test_a_pointer_that_resolves_beside_the_doc_is_not_broken(monkeypatch) -> None:
    """Both conventions are in the docs: `docs/decisions.md` from the repo root
    and `decisions.md` from beside the file that cites it. A pointer is broken
    only when neither lands."""
    worktree = _Worktree(
        pointers=("docs/README.md:`decisions.md`",), present=("docs/decisions.md",),
    ).install(monkeypatch)

    assert _findings(_run(worktree), gates.POINTERS) == []


def test_prose_in_backticks_never_reaches_the_filesystem(monkeypatch) -> None:
    """Every doc is grepped, so most of what comes back is commands and symbol
    names. They are dropped before the `ls`, which is both why the gate is
    cheap and why it does not accuse a doc of pointing at `npm test`."""
    worktree = _Worktree(
        pointers=("docs/README.md:`npm run build`", "docs/README.md:`DispatchResult`"),
    ).install(monkeypatch)

    assert _run(worktree).findings == []
    assert not worktree.ran(["ls"])


def test_a_project_with_no_docs_is_not_this_gates_problem(monkeypatch) -> None:
    """grep exits 1 with nothing matched and 2 with no `docs/` at all. Neither
    is a finding — an unmapped project is the mapper's business."""
    worktree = _Worktree(pointers=()).install(monkeypatch)

    assert _run(worktree).findings == []
