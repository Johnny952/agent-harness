import subprocess

import pytest

from dispatcher import docker_exec, project_docs


def _fake_cat(text: str, returncode: int = 0):
    """A run_docker_exec that answers `cat docs/README.md` with `text`."""
    def fake_run_docker_exec(container, workdir, command, **kwargs):
        assert command == ["cat", project_docs.INDEX]
        return subprocess.CompletedProcess(command, returncode, stdout=text, stderr="")
    return fake_run_docker_exec


def test_the_mapper_writes_the_same_tree_the_other_writers_do() -> None:
    """project_docs cannot import docker_exec's role set without a cycle, so
    the mapper's name is spelled out there. If the two drift the mapper gets a
    throwaway review checkout and its docs are deleted with it."""
    assert project_docs.MAPPER_ROLE in docker_exec.WRITER_ROLES


def test_the_auditor_writes_the_same_tree_too() -> None:
    """It files the indexes last, after the revisor has approved. A review
    checkout would be deleted with its entries in it — measured 2026-09-24."""
    assert "auditor" in docker_exec.WRITER_ROLES


def test_the_auditor_commits_only_the_docs_it_was_asked_for() -> None:
    """Being a writer that runs after the review is what makes the scope
    necessary: everything else it touched would land unread."""
    assert project_docs.commit_scope("auditor") == (project_docs.DOCS_DIR,)


@pytest.mark.parametrize("role", ["cartografo", "arquitecto", "implementador", "revisor"])
def test_every_other_role_commits_whatever_it_produced(role: str) -> None:
    assert project_docs.commit_scope(role) is None


def test_no_role_commits_the_charter() -> None:
    """The exclusion takes no role, unlike the scope: a mapping invites an
    exception, and the charter's whole premise is that there is none."""
    assert project_docs.commit_excludes() == (project_docs.CHARTER,)


def test_the_charter_is_inside_the_scope_the_auditor_was_given() -> None:
    """Which is why the exclusion has to exist at all: the one role whose duty
    is writing docs is the one role whose scope already covers the charter."""
    assert project_docs.CHARTER.startswith(f"{project_docs.DOCS_DIR}/")
    assert project_docs.commit_scope("auditor") == (project_docs.DOCS_DIR,)


def test_implementation_doc_is_one_file_per_task() -> None:
    assert project_docs.implementation_doc("task-7") == "docs/implementations/task-7.md"


def test_index_path_hangs_off_the_project_checkout() -> None:
    assert project_docs.index_path("/data/projects/myproj") == "/data/projects/myproj/docs/README.md"


@pytest.mark.parametrize("exists", [True, False])
def test_has_index_asks_the_container_for_the_index(monkeypatch, exists: bool) -> None:
    asked = {}

    def fake_path_exists(container, path):
        asked["container"] = container
        asked["path"] = path
        return exists

    monkeypatch.setattr(project_docs.docker_exec, "path_exists", fake_path_exists)

    assert project_docs.has_index("agent-cuenta1", "/data/projects/myproj") is exists
    assert asked == {"container": "agent-cuenta1", "path": "/data/projects/myproj/docs/README.md"}


def test_read_commands_reads_the_index_frontmatter(monkeypatch) -> None:
    index = (
        "---\n"
        "build: npm run build\n"
        "test: npm test -- --run\n"
        "---\n\n"
        "# myproj\n\nSome prose about the project.\n"
    )
    monkeypatch.setattr(project_docs.docker_exec, "run_docker_exec", _fake_cat(index))

    assert project_docs.read_commands("agent-cuenta1", "/data/projects/myproj") == {
        "build": ("npm run build",),
        "test": ("npm test -- --run",),
    }


def test_read_commands_reads_lists_and_the_keys_no_mapper_writes(monkeypatch) -> None:
    index = (
        "---\n"
        "install: cd front && bun install --frozen-lockfile\n"
        "test:\n"
        "  - python3 -m pytest\n"
        "  - cd front && bun run typecheck\n"
        "lint: cd front && bun run lint\n"
        "---\n"
    )
    monkeypatch.setattr(project_docs.docker_exec, "run_docker_exec", _fake_cat(index))

    assert project_docs.read_commands("agent-cuenta1", "/p") == {
        "install": ("cd front && bun install --frozen-lockfile",),
        "test": ("python3 -m pytest", "cd front && bun run typecheck"),
        "lint": ("cd front && bun run lint",),
    }


def test_read_commands_returns_only_the_keys_that_are_there(monkeypatch) -> None:
    """A map that could not establish the build command omits the key rather
    than guessing one, so a partial answer has to survive."""
    monkeypatch.setattr(
        project_docs.docker_exec, "run_docker_exec", _fake_cat("---\ntest: pytest\n---\n"),
    )

    assert project_docs.read_commands("agent-cuenta1", "/p") == {"test": ("pytest",)}


@pytest.mark.parametrize(
    "index",
    [
        "# myproj\n\nNo frontmatter at all.\n",
        "---\ntest: [pytest, -q\n---\n",          # unparseable YAML
        "---\n- pytest\n---\n",                    # a list, not a mapping
        "---\ntest: 44\n---\n",                    # not a command string
        "---\ntest: '   '\n---\n",                 # blank
        "---\nbuild: make\n",                      # never closed
        "---\ntest: []\n---\n",                   # an empty list
        "---\ntest: [pytest, 44]\n---\n",         # a list with a non-string
        "---\ntest: [pytest, '  ']\n---\n",       # a list with a blank
    ],
    ids=[
        "none", "malformed", "not-a-mapping", "not-a-string", "blank", "unterminated",
        "empty-list", "list-with-a-number", "list-with-a-blank",
    ],
)
def test_read_commands_is_empty_when_the_index_says_nothing_runnable(monkeypatch, index: str) -> None:
    """The gate that runs these has no model in the loop: an unusable index
    means no command, never a half-parsed one."""
    monkeypatch.setattr(project_docs.docker_exec, "run_docker_exec", _fake_cat(index))

    assert project_docs.read_commands("agent-cuenta1", "/p") == {}


def test_read_commands_is_empty_when_there_is_no_index(monkeypatch) -> None:
    monkeypatch.setattr(
        project_docs.docker_exec, "run_docker_exec", _fake_cat("", returncode=1),
    )

    assert project_docs.read_commands("agent-cuenta1", "/p") == {}


@pytest.mark.parametrize(
    "role", ["cartografo", "arquitecto", "implementador", "revisor", "auditor"],
)
def test_every_role_with_a_docs_duty_is_told_how_to_cite(role: str) -> None:
    """A pointer by line number is a pointer that starts lying at the next
    commit, and every one of these roles writes pointers."""
    assert "never by line number" in project_docs.duties(role, "task-1")


@pytest.mark.parametrize(
    "role", ["cartografo", "arquitecto", "implementador", "revisor", "auditor"],
)
def test_every_role_with_a_docs_duty_is_told_to_batch_its_calls(role: str) -> None:
    """Every turn re-reads the whole context, so a serial read costs every
    role the same way (token-economy P1)."""
    assert "go in the same turn" in project_docs.duties(role, "task-1")


def test_only_the_implementador_is_told_to_check_scope_by_stat() -> None:
    """The full diff is the revisor's read: told to skip it, the revisor would
    review a summary (token-economy P3)."""
    assert "git diff --stat" in project_docs.duties("implementador", "task-1")
    assert "offset/limit" in project_docs.duties("implementador", "task-1")
    for role in ("cartografo", "arquitecto", "revisor", "auditor"):
        assert "git diff --stat" not in project_docs.duties(role, "task-1")


def test_only_the_implementador_is_told_how_to_read_the_results_log() -> None:
    """The one role seen appending a Results row, by reading 46k characters of
    rows to learn the format (token-economy P2)."""
    implementador = project_docs.duties("implementador", "task-1")

    assert "`tail -n 1 docs/ROADMAP.md | cut -c1-400`" in implementador
    assert "never `sed` or `grep` whole rows" in implementador
    for role in ("cartografo", "arquitecto", "revisor", "auditor"):
        assert "docs/ROADMAP.md" not in project_docs.duties(role, "task-1")


def test_duties_are_empty_for_a_role_that_owes_the_docs_nothing() -> None:
    """A role added elsewhere degrades to saying nothing, not to a KeyError in
    the middle of a dispatch."""
    assert project_docs.duties("inventado", "task-1") == ""


def test_the_arquitecto_is_pointed_at_the_index_and_owns_the_decisions() -> None:
    duties = project_docs.duties("arquitecto", "task-1")

    assert project_docs.INDEX in duties
    assert project_docs.DECISIONS in duties
    assert "trigger" in duties
    # Appended and struck through, never rewritten: an ADR that is edited away
    # takes the reason the decision was made with it.
    assert "Never rewrite" in duties


def test_the_implementador_writes_this_task_s_implementation_doc() -> None:
    duties = project_docs.duties("implementador", "task-42")

    assert "docs/implementations/task-42.md" in duties
    # Debt and learnings travel as handoff fields, because the auditor is the
    # only writer of the indexes.
    assert "`debt`" in duties and "`learnings`" in duties


def test_only_the_auditor_is_told_to_write_the_indexes() -> None:
    auditor = project_docs.duties("auditor", "task-1")

    assert project_docs.LEARNINGS_INDEX in auditor
    assert project_docs.DEBT_INDEX in auditor
    assert "only phase that writes the indexes" in auditor
    for role in ("arquitecto", "implementador", "revisor"):
        assert project_docs.LEARNINGS_INDEX not in project_docs.duties(role, "task-1")


def test_the_revisor_treats_an_undocumented_contract_change_as_a_finding() -> None:
    revisor = project_docs.duties("revisor", "task-1")

    assert "is a finding" in revisor
    assert "stale" in revisor


def test_the_mapper_is_told_what_to_write_and_not_to_touch_the_code() -> None:
    mapper = project_docs.duties(project_docs.MAPPER_ROLE, "task-1")

    for path in (project_docs.INDEX, project_docs.ARCHITECTURE, project_docs.BUSINESS):
        assert path in mapper
    assert "Do not change its code" in mapper
    # The two keys the dispatcher parses back out, named in the prompt that
    # writes them.
    for key in project_docs.MAPPED_KEYS:
        assert f"`{key}:`" in mapper


def test_the_mapper_is_told_its_turn_budget_when_it_has_one() -> None:
    """The budget is a `--max-turns` cut-off, not a suggestion: a mapper that
    does not know the number spends it all on the first module."""
    bounded = project_docs.duties(project_docs.MAPPER_ROLE, "task-1", max_turns=40)
    unbounded = project_docs.duties(project_docs.MAPPER_ROLE, "task-1")

    assert "40 turns and no more" in bounded
    assert "turns and no more" not in unbounded
