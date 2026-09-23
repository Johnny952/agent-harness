# dispatcher/gates.py
"""What the dispatcher checks itself, between the implementador and the revisor.

A revisor call costs quota, and the cheapest findings are the ones no model has
to make: whether a change that touched code touched a test, whether the
project's own test command still passes, whether a contract moved without its
documentation, whether a pointer in `docs/` still lands on a file. All four run
through `docker exec` with no `claude` in the loop, so they spend nothing, they
are out of reach of the phase they judge — a role cannot talk its way past a
shell — and they check the claim that the tests pass instead of believing it.

Findings come at three levels, because the answers are not equally cheap:

- `BLOCKING` — the tests failed. The revisor is not called at all; the round
  goes around again with the log's path, which is a whole review call saved.
- `ASK` — code changed and no test did. Worth one `--resume` into the session
  that just ended, asking for a test or a reason, and the reason travels to the
  revisor, which is the thing that can judge it.
- `NOTE` — everything else. It rides along in the task file for the revisor and
  the auditor to weigh, and costs no extra call.

Every gate errs toward saying nothing. A false finding costs a round of real
quota and teaches the roles to argue with the dispatcher; a missed one costs
what the harness was already paying, a revisor that has to notice it.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import subprocess

from dispatcher import docker_exec, project_docs

logger = logging.getLogger(__name__)

BLOCKING = "blocking"
ASK = "ask"
NOTE = "note"

#: Gate names, as they appear in the task file and in the logs.
TESTS_IN_DIFF = "tests-in-diff"
TESTS_RUN = "tests-run"
CONTRACT_DOCS = "contract-docs"
POINTERS = "pointers"

# git's tolerated-error checks elsewhere in the harness match English messages,
# and so does the `ls` parsing below. Every command whose *output* this module
# reads gets the C locale; the project's own test command does not, because
# that one is run the way the project expects to run it.
_C_LOCALE = {"LC_ALL": "C"}


@dataclasses.dataclass(frozen=True)
class Finding:
    gate: str
    detail: str
    level: str = NOTE


@dataclasses.dataclass
class Report:
    findings: list[Finding] = dataclasses.field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(f.level == BLOCKING for f in self.findings)

    @property
    def needs_answer(self) -> bool:
        """Whether any of this is worth one `--resume` before review."""
        return any(f.level in (BLOCKING, ASK) for f in self.findings)

    def render(self) -> str:
        """The gate section appended under the phase's handoff."""
        if not self.findings:
            return ""
        return "\n".join(
            ["**Dispatcher gates**"]
            + [f"- `{f.gate}` ({f.level}) — {f.detail}" for f in self.findings]
        )

    def resume_prompt(self) -> str:
        """The one `--resume` a phase gets when a gate wants an answer.

        Exactly one, like the handoff's shrink retry, and for the same reason:
        a second costs as much as the phase it is correcting. It asks for the
        structured return again because the retry's return is the one that
        lands in the task file — without the schema the fix comes back as
        prose and the whole handoff degrades to a clamped paragraph.
        """
        asked = [f for f in self.findings if f.level in (BLOCKING, ASK)]
        lines = "\n".join(f"- {f.detail}" for f in asked)
        return (
            "Before this goes to review the dispatcher ran its own checks on the "
            f"worktree, without a model, and they came back with this:\n{lines}\n\n"
            "Fix what you can in this session. If a check is wrong, or the work "
            "genuinely does not need what it asks for, say so in one line under "
            "`risks` — the revisor reads it and decides. Then send the same "
            "structured return again, updated and no longer than before. The "
            "checks run again after this, so a claim that they now pass is "
            "checked rather than taken."
        )


def _sample(paths: list[str], limit: int = 5) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:limit])
    extra = len(paths) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown


# --------------------------------------------------------------------------
# What changed
# --------------------------------------------------------------------------

def _fork_point(container: str, workdir: str, project_dir: str) -> str | None:
    """The commit this task's branch grew out of, or None.

    Read the same way `merge_task_branch` reads its target — from the branch
    the project's own checkout sits on — so the gate and the merge agree on
    what "this task's work" means without anyone configuring it twice.

    None means skip the gates that need a diff, never fall back to `HEAD`:
    against HEAD the diff would hold only the round's uncommitted edits, so a
    round 2 that fixes a line would look like code with no test even when the
    test landed in round 1.
    """
    target = docker_exec.current_branch(container, project_dir)
    if target is None:
        return None
    proc = docker_exec.run_docker_exec(
        container, workdir, ["git", "merge-base", "HEAD", target], env=_C_LOCALE,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def changed_paths(container: str, workdir: str, base: str) -> list[str]:
    """Everything this task's branch has touched, committed or not.

    Three states have to be counted, because the gates run before the
    dispatcher commits the phase: what earlier rounds committed, what this
    round left in the working tree, and the files it created — a brand new
    test is untracked, and a diff alone would miss exactly the file whose
    absence the first gate is looking for.
    """
    paths: list[str] = []
    for command in (
        ["git", "diff", "--name-only", base, "--"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        proc = docker_exec.run_docker_exec(container, workdir, command, env=_C_LOCALE)
        if proc.returncode != 0:
            logger.warning("gates: %s failed: %s", " ".join(command), proc.stderr.strip()[:200])
            continue
        paths += [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return list(dict.fromkeys(paths))


# --------------------------------------------------------------------------
# Gate 1 — a change to code is a change to a test
# --------------------------------------------------------------------------

_CODE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte",
    ".go", ".rs", ".rb", ".java", ".kt", ".swift", ".scala", ".cs", ".php",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".sh", ".sql",
})

_TEST_DIR_NAMES = frozenset({"test", "tests", "spec", "specs", "__tests__", "e2e"})


def is_test(path: str) -> bool:
    """Whether a path looks like a test, generously.

    Generous on purpose, and in the one direction that is safe: a test this
    misses becomes a wrong accusation and a wasted round, while a file it
    wrongly counts as a test only means the revisor makes the call instead of
    the gate — which is what the revisor is for.
    """
    parts = path.split("/")
    if any(part.lower() in _TEST_DIR_NAMES for part in parts[:-1]):
        return True
    stem = parts[-1].rsplit(".", 1)[0].lower()
    return stem.startswith(("test", "spec", "conftest")) or stem.endswith(
        ("test", "tests", "spec", "specs")
    )


def is_code(path: str) -> bool:
    if path.startswith(f"{project_docs.DOCS_DIR}/"):
        return False
    return os.path.splitext(path)[1].lower() in _CODE_SUFFIXES


def _tests_in_diff(changed: list[str]) -> list[Finding]:
    code = [p for p in changed if is_code(p) and not is_test(p)]
    if not code:
        return []
    if any(is_test(p) for p in changed):
        return []
    return [Finding(
        TESTS_IN_DIFF,
        f"code changed and no test did: {_sample(code)}. Add a test, or say in "
        "one line why this change does not need one.",
        ASK,
    )]


# --------------------------------------------------------------------------
# Gate 2 — the project's own tests pass
# --------------------------------------------------------------------------

#: Output that means the suite never ran, rather than ran and failed. A fresh
#: worktree has no `node_modules` and no virtualenv (the deferred "dependencies
#: in fresh worktrees" gate), and blocking a round on that would burn real
#: quota on a problem no implementador can fix from inside its session.
_COULD_NOT_RUN_MARKERS = (
    "command not found",
    "cannot find module",
    "cannot find package",
    "no module named",
    "modulenotfounderror",
    "missing script",
    "executable not found",
    "no such file or directory",
)

_LOG_TAIL_BYTES = 20000
_INLINE_TAIL_CHARS = 500


def _could_not_run(returncode: int, output: str) -> bool:
    if returncode == 127:
        return True
    lowered = output.lower()
    return any(marker in lowered for marker in _COULD_NOT_RUN_MARKERS)


def _write_log(log_path: str | None, command: str, output: str) -> bool:
    """Park the test output where a later round can read it by path.

    It goes to the task's scratch directory, which the dispatcher and the
    agents mount at the same path, so a finding can cite it and the next
    implementador can open it. The repo cannot hold it — a reviewing
    checkout is rebuilt every round — and the handoff cannot carry it.
    """
    if not log_path:
        return False
    if len(output) > _LOG_TAIL_BYTES:
        output = f"[… {len(output) - _LOG_TAIL_BYTES} chars omitted …]\n" + output[-_LOG_TAIL_BYTES:]
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            f.write(f"$ {command}\n\n{output}")
    except OSError as exc:
        logger.warning("gates: could not write %s: %s", log_path, exc)
        return False
    return True


def _run_tests(
    container: str,
    workdir: str,
    project_dir: str,
    timeout_seconds: int,
    log_path: str | None,
) -> list[Finding]:
    command = project_docs.read_commands(container, project_dir).get("test")
    if not command:
        # No command recorded means no gate, not a failure: a project nobody
        # mapped has nothing to run, and inventing a command would be worse
        # than running none.
        logger.info("gates: no `test:` in %s, skipping the test gate", project_docs.INDEX)
        return []
    try:
        proc = docker_exec.run_docker_exec(
            container, workdir, ["sh", "-c", command], timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # A NOTE, not a block: three rounds of implementador quota is a steep
        # price for a suite that is merely slow, and the revisor is told either
        # way. If this shows up often, the timeout is the thing to move.
        return [Finding(
            TESTS_RUN,
            f"`{command}` did not finish within {timeout_seconds}s, so whether it "
            "passes is unknown. Check it yourself.",
            NOTE,
        )]
    if proc.returncode == 0:
        return []

    output = (proc.stdout or "") + (proc.stderr or "")
    if _could_not_run(proc.returncode, output):
        return [Finding(
            TESTS_RUN,
            f"`{command}` could not run in this worktree (exit {proc.returncode}); it "
            "looks like missing dependencies rather than a failing test, so it is "
            "not being treated as one.",
            NOTE,
        )]

    detail = f"`{command}` failed (exit {proc.returncode})"
    if _write_log(log_path, command, output):
        detail += f". The output is at `{log_path}`"
    else:
        # No log to point at, so the finding carries the end of the output
        # itself — the tail, because that is where a runner prints what failed.
        detail += f": {output.strip()[-_INLINE_TAIL_CHARS:]}"
    return [Finding(TESTS_RUN, detail + ".", BLOCKING)]


# --------------------------------------------------------------------------
# Gate 3 — a contract that moves takes its documentation with it
# --------------------------------------------------------------------------

_CONTRACT_MARKERS = ("openapi", "swagger")
_CONTRACT_NAMES = frozenset({".env.example", "schema.prisma"})
_CONTRACT_SUFFIXES = frozenset({".proto", ".graphql", ".gql"})
_CONTRACT_DIRS = frozenset({"migrations", "migration"})


def is_contract(path: str) -> bool:
    """Whether a path is something another program depends on by name.

    Only what a path can say. A public export or a CLI flag is just as much a
    contract and neither is visible from a filename, so those stay where they
    already were — in the revisor's duties, which name them — and this gate
    stays narrow rather than pretending to a coverage it does not have.
    """
    parts = path.lower().split("/")
    name = parts[-1]
    if any(part in _CONTRACT_DIRS for part in parts[:-1]):
        return True
    if name in _CONTRACT_NAMES or os.path.splitext(name)[1] in _CONTRACT_SUFFIXES:
        return True
    return any(marker in name for marker in _CONTRACT_MARKERS)


def is_doc(path: str) -> bool:
    return path.startswith(f"{project_docs.DOCS_DIR}/") or path.lower().endswith(".md")


def _contracts_without_docs(changed: list[str]) -> list[Finding]:
    contracts = [p for p in changed if is_contract(p)]
    if not contracts or any(is_doc(p) for p in changed):
        return []
    return [Finding(
        CONTRACT_DOCS,
        f"a contract changed and no documentation did: {_sample(contracts)}. Say "
        "where this is written down, or write it down.",
        NOTE,
    )]


# --------------------------------------------------------------------------
# Gate 4 — the pointers in docs/ still land somewhere
# --------------------------------------------------------------------------

#: Backticked spans and markdown link targets. Everything the roles are told to
#: cite with is one of the two, and both are cheap for grep to find.
_POINTER_PATTERN = r"`[^`]+`|\]\([^)]+\)"
_POINTER_CHUNK = 100
_POINTER_LIMIT = 400
_POINTER_REPORT_LIMIT = 8
_HAS_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_LS_MISSING = re.compile(r"^ls: (?:cannot access )?'?(.+?)'?: No such file")


def pointer_token(raw: str) -> str | None:
    """A citation's path, or None when it is not one.

    Rejects far more than it accepts: a false broken pointer sends the auditor
    after a file that was never supposed to exist, which is worse than missing
    a real one. Prose in backticks, urls, anchors, globs and bare filenames all
    fall out here, and what survives is a path with a slash and an extension.
    """
    token = raw.strip()
    if token.startswith("`") and token.endswith("`"):
        token = token[1:-1]
    elif token.startswith("](") and token.endswith(")"):
        token = token[2:-1]
    # Docs cite `path#anchor`, never `path:line` — the anchor survives an edit
    # and the line number does not — so only the anchor has to be stripped.
    token = token.split("#", 1)[0].strip()
    if token.startswith("./"):
        token = token[2:]
    if not token or "/" not in token:
        return None
    if "://" in token or token.startswith(("-", "/", "~", "mailto:")):
        return None
    if any(ch in token for ch in " \t*?\"'<>|$(){}[]"):
        return None
    if not _HAS_EXTENSION.search(token.rsplit("/", 1)[-1]):
        return None
    return token


def _candidates(doc: str, token: str) -> list[str]:
    """Where a citation could be resolving from.

    Both, because both conventions are in the docs: `docs/decisions.md` names
    the path from the repo root, `decisions.md` names it from beside the file
    that cites it, and the pointer is only broken when neither lands.
    """
    found = []
    for path in (token, os.path.join(os.path.dirname(doc), token)):
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or normalized in found:
            continue
        found.append(normalized)
    return found


def _missing(container: str, workdir: str, paths: list[str]) -> set[str]:
    """Which of these do not exist, asked in as few calls as possible.

    `ls -d --` rather than a shell test per path: one exec per hundred paths
    instead of one per path, no shell to quote anything into, and `--` so a
    path can never be read as a flag.
    """
    missing: set[str] = set()
    for start in range(0, len(paths), _POINTER_CHUNK):
        chunk = paths[start:start + _POINTER_CHUNK]
        proc = docker_exec.run_docker_exec(
            container, workdir, ["ls", "-d", "--", *chunk], env=_C_LOCALE,
        )
        if proc.returncode == 0:
            continue
        for line in proc.stderr.splitlines():
            match = _LS_MISSING.match(line.strip())
            if match:
                missing.add(match.group(1))
    return missing


def _broken_pointers(container: str, workdir: str) -> list[Finding]:
    proc = docker_exec.run_docker_exec(
        container, workdir,
        ["grep", "-r", "-o", "-E", _POINTER_PATTERN, project_docs.DOCS_DIR],
        env=_C_LOCALE,
    )
    if proc.returncode != 0:
        # 1 is "nothing matched", 2 is "there is no docs/". Neither is a
        # finding: a project without docs is the mapper's problem, not this
        # gate's.
        return []

    cited: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        doc, _, raw = line.partition(":")
        token = pointer_token(raw) if doc and raw else None
        if token is None:
            continue
        cited.setdefault(f"{doc}\t{token}", _candidates(doc, token))
        if len(cited) >= _POINTER_LIMIT:
            break

    candidates = sorted({path for paths in cited.values() for path in paths})
    if not candidates:
        return []
    missing = _missing(container, workdir, candidates)

    broken = [
        key.split("\t") for key, paths in cited.items()
        if paths and all(path in missing for path in paths)
    ]
    if not broken:
        return []
    shown = [f"`{doc}` points at `{token}`" for doc, token in broken[:_POINTER_REPORT_LIMIT]]
    extra = len(broken) - _POINTER_REPORT_LIMIT
    if extra > 0:
        shown.append(f"and {extra} more")
    return [Finding(
        POINTERS,
        "; ".join(shown) + " — those paths are not there. Fix the pointer or the path.",
        NOTE,
    )]


# --------------------------------------------------------------------------

def run(
    container: str,
    workdir: str,
    project_dir: str,
    *,
    test_timeout_seconds: int,
    test_log_path: str | None = None,
) -> Report:
    """Every gate, against the worktree a phase just finished in."""
    findings: list[Finding] = []

    base = _fork_point(container, workdir, project_dir)
    if base is None:
        logger.info("gates: no fork point for this worktree, skipping the diff gates")
    else:
        changed = changed_paths(container, workdir, base)
        findings += _tests_in_diff(changed)
        findings += _contracts_without_docs(changed)

    findings += _run_tests(container, workdir, project_dir, test_timeout_seconds, test_log_path)
    # Every doc, not only the ones this task touched: the pointer that breaks
    # is usually in a file nobody opened, because what moved was the thing it
    # pointed at.
    findings += _broken_pointers(container, workdir)
    return Report(findings)
