"""The per-project, per-role allowlist a phase's `--allowed-tools` grows by.

Step 5 of `docs/plans/command-requests.md` (decision 6; `docs/decisions.md`
ADR 121). An *allow* verdict on a filed request runs it as *run* does and also
writes a standing entry here, so the next phase of that role in that project
runs the command itself instead of filing it again. When the dispatcher builds
a phase's flags it appends the project's list for the phase's role to the
global `allowed_tools` (`merged_allowed_tools`); it never touches the global
list.

An entry is a Claude CLI permission pattern: `Bash(<argv joined by spaces>)`
for the exact command, or `Bash(<first N tokens>:*)` for a prefix. Decision 7
overrides any verdict, so a pattern is written only when it says what the
argv says (`pattern_problems`): every token is plain, with no whitespace,
quote, parenthesis, glob or shell character, because the CLI reads the
pattern as a shell command and the argv never went near a shell. A prefix is
held to more (`prefix_problems`, ADR 121 has the reasoning): at least two
tokens, no option token, and a head that is not a shell, an interpreter, a
privilege or docker binary, a launcher that runs whatever follows it, or a
`git` subcommand outside a short local list.

Every stored pattern is checked again by the same rules each time it is
merged, so an entry written by hand, or by an older rule, that no longer
passes is skipped and logged, never passed to a phase.

The file is `<state_dir>/allowlists/<slug>.json`, beside the ADR counters
(`adr_numbers`): `state_dir` is the dispatcher's own volume, read-only to the
api and not mounted in any agent container, so no phase can write itself a
grant. It is written the way the counters are, under an `flock` held around
the read-modify-write and replaced in one step.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
from pathlib import Path

from dispatcher import command_targets, context_transfer

logger = logging.getLogger(__name__)

#: The subdirectory of `state_dir` the lists live in, one file per project.
STATE_SUBDIR = "allowlists"

#: What a token may hold to be written into a pattern unchanged: no
#: whitespace, quote, parenthesis, glob, `$`, backtick, `~`, `#`, `!`, `{}`,
#: `;`, `&`, `|`, `<` or `>`, so the shell the CLI matches against reads it as
#: the one word the argv had. `*` is out too, so `:*` cannot occur inside one.
_PLAIN_TOKEN = re.compile(r"^[A-Za-z0-9_./@%+=,:-]+$")

#: The fewest tokens a prefix may have: a command and at least one word that
#: narrows it.
MIN_PREFIX_TOKENS = 2

#: Heads a prefix never starts with, by basename: each runs, or can be told
#: to run, a command its arguments name, so a prefix on it covers commands
#: decision 7 refuses (`python -c`, `env sudo ...`, `xargs git push`).
_NEVER_HEADS = frozenset({
    *command_targets._SHELLS, *command_targets._PRIVILEGE, *command_targets._DOCKER_BINARIES,
    "env", "xargs", "timeout", "nice", "nohup", "exec", "command", "builtin", "eval",
    "time", "watch", "strace", "ltrace", "chroot", "setsid", "stdbuf", "ionice", "flock",
    "unshare", "nsenter", "script", "find", "parallel", "ssh", "scp", "sftp", "rsync",
    "node", "nodejs", "deno", "perl", "ruby", "irb", "php", "lua", "luajit", "tclsh",
    "Rscript", "awk", "gawk", "mawk", "nawk", "sed", "vi", "vim", "nvim", "emacs",
    "npx", "uvx", "bunx", "pnpx",
})
#: Interpreters with a version in the name: `python3`, `python3.12`, `pypy3`.
_INTERPRETER = re.compile(r"^(python|pypy)[0-9.]*$")

#: Launchers spelt in more than one token. A prefix that ends inside one is
#: refused; one that names the program after it is judged by that program.
_WRAPPERS: tuple[tuple[str, ...], ...] = (
    ("uv", "run"), ("uv", "tool", "run"), ("npm", "exec"), ("npm", "x"),
    ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "exec"), ("yarn", "dlx"),
    ("bun", "x"), ("bun", "run"), ("poetry", "run"), ("pdm", "run"), ("pipenv", "run"),
    ("hatch", "run"), ("rye", "run"), ("bundle", "exec"), ("pipx", "run"),
    ("conda", "run"), ("mise", "exec"), ("asdf", "exec"),
)

#: The `git` subcommands a prefix may name: local reads and edits of the
#: worktree. Not `config` (it can set a hook path or an ssh command the
#: dispatcher's own git would run), `remote`, `submodule` or anything that
#: reaches a remote.
_GIT_LOCAL_SUBCOMMANDS = frozenset({
    "status", "diff", "log", "show", "add", "commit", "restore", "stash", "branch",
    "rev-parse", "ls-files", "grep", "blame", "switch", "checkout", "mv", "rm", "reset",
    "tag", "merge", "rebase", "cherry-pick", "shortlog", "describe",
})


class AllowRefused(ValueError):
    """An argv or a prefix that cannot be written as a standing pattern, and why."""


def pattern_problems(tokens: list[str]) -> list[str]:
    """Why these tokens cannot be written into a `Bash(...)` pattern as they are."""
    if not tokens:
        return ["the command is empty"]
    problems = []
    for token in tokens:
        if not token:
            problems.append("an empty token cannot be written into a pattern")
        elif not _PLAIN_TOKEN.match(token):
            problems.append(f"token {token!r} holds a character a Bash pattern would read as shell")
    if tokens[0] and "=" in tokens[0]:
        # The argv ran a program by that name; the shell would read an assignment.
        problems.append(f"the first token {tokens[0]!r} would read as a variable assignment")
    if tokens[0].startswith("-"):
        problems.append(f"the first token {tokens[0]!r} is an option, not a command")
    # The same rules as at filing, on the tokens alone; the cwd rule has no
    # cwd to judge here, so it is given one inside itself.
    problems += command_targets.denylist_problems(list(tokens), "agent", "/", "/")
    return problems


def _head_problem(tokens: list[str]) -> str | None:
    """Why a prefix's head could run a command its own words do not name."""
    for wrapper in _WRAPPERS:
        if tuple(tokens[:len(wrapper)]) == wrapper:
            rest = tokens[len(wrapper):]
            if not rest:
                return f"`{' '.join(wrapper)}` runs whatever follows it"
            return _head_problem(rest)
    head = command_targets._base(tokens[0])
    if head in _NEVER_HEADS or _INTERPRETER.match(head):
        return f"a prefix on `{head}` covers commands it runs from its arguments"
    if head == "git":
        sub = tokens[1] if len(tokens) > 1 else None
        if sub not in _GIT_LOCAL_SUBCOMMANDS:
            return (
                f"a `git` prefix names one of its local subcommands "
                f"({', '.join(sorted(_GIT_LOCAL_SUBCOMMANDS))}), not {sub!r}"
            )
    return None


def prefix_problems(tokens: list[str]) -> list[str]:
    """Why these tokens cannot stand as a `:*` prefix. Empty means they can."""
    problems = pattern_problems(tokens)
    if problems:
        return problems
    if len(tokens) < MIN_PREFIX_TOKENS:
        problems.append(
            f"a prefix holds at least {MIN_PREFIX_TOKENS} tokens, a command and a word that "
            f"narrows it; {' '.join(tokens)!r} alone covers every use of it"
        )
    options = [token for token in tokens[1:] if token.startswith("-")]
    if options:
        problems.append(f"a prefix ends before any option ({options[0]!r} leaves its value open)")
    why = _head_problem(tokens)
    if why is not None:
        problems.append(why)
    return problems


def pattern_for(argv: list[str], prefix: int | None = None) -> str:
    """The pattern an *allow* on `argv` writes, or `AllowRefused` with every reason.

    `prefix` is how many of the argv's first tokens the pattern keeps, with
    `:*` after them; None keeps all of them and no `:*`, the exact command.
    The whole argv is checked either way: an exact pattern is the argv, and a
    prefix is only offered on a command that could itself be allowed.
    """
    argv = [str(item) for item in argv]
    problems = pattern_problems(argv)
    if prefix is None:
        if problems:
            raise AllowRefused("; ".join(problems))
        return f"Bash({' '.join(argv)})"
    if not 1 <= prefix <= len(argv):
        raise AllowRefused(f"--prefix {prefix} is not between 1 and the argv's {len(argv)} tokens")
    problems += [item for item in prefix_problems(argv[:prefix]) if item not in problems]
    if problems:
        raise AllowRefused("; ".join(problems))
    return f"Bash({' '.join(argv[:prefix])}:*)"


def stored_pattern_problems(pattern: object) -> list[str]:
    """The rules `pattern_for` holds a new pattern to, on one read back from the file."""
    if not isinstance(pattern, str) or not (pattern.startswith("Bash(") and pattern.endswith(")")):
        return [f"{pattern!r} is not a Bash(...) pattern"]
    inner = pattern[len("Bash("):-1]
    if inner.endswith(":*"):
        return prefix_problems(inner[:-2].split(" "))
    return pattern_problems(inner.split(" "))


# --- the file -------------------------------------------------------------


def _path(state_dir: str, slug: str) -> str:
    if not context_transfer._is_single_path_component(slug):
        raise ValueError(f"project slug {slug!r} is not a single path component")
    return os.path.join(state_dir, STATE_SUBDIR, f"{slug}.json")


def _read(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    data = json.loads(Path(path).read_text())
    return data if isinstance(data, dict) else {}


@contextlib.contextmanager
def _locked(state_dir: str, slug: str):
    """The project's list lock, as `adr_numbers._locked` takes its counter's."""
    directory = os.path.join(state_dir, STATE_SUBDIR)
    Path(directory).mkdir(parents=True, exist_ok=True)
    fd = os.open(os.path.join(directory, f"{slug}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(path: str, data: dict) -> None:
    context_transfer._write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def entries(state_dir: str, slug: str, role: str | None = None) -> dict[str, list[dict]]:
    """The project's entries by role, or only `role`'s. A missing file is none."""
    data = _read(_path(state_dir, slug))
    found = {
        name: [item for item in items if isinstance(item, dict)]
        for name, items in data.items() if isinstance(items, list)
    }
    if role is not None:
        return {role: found.get(role, [])}
    return found


def add(state_dir: str, slug: str, role: str, pattern: str, by: str, at: str, source: dict) -> dict:
    """Write one entry for `role`, or return the one already there for `pattern`.

    `source` is the request it came from: `task_id`, `role`, `n`, `filed_at`.
    The pattern is checked again here, so nothing that fails the rules is
    ever written, whoever calls.
    """
    problems = stored_pattern_problems(pattern)
    if problems:
        raise AllowRefused("; ".join(problems))
    if not role:
        raise ValueError("an allowlist entry needs a role")
    path = _path(state_dir, slug)
    with _locked(state_dir, slug):
        data = _read(path)
        items = [item for item in data.get(role) or [] if isinstance(item, dict)]
        for item in items:
            if item.get("pattern") == pattern:
                return item
        entry = {"pattern": pattern, "by": by, "at": at, "from": dict(source)}
        data[role] = [*items, entry]
        _write(path, data)
    logger.info("allowlist %s/%s: added %s by %s", slug, role, pattern, by)
    return entry


def revoke(state_dir: str, slug: str, role: str, pattern: str) -> bool:
    """Remove `role`'s entry for `pattern`; False when there was none."""
    path = _path(state_dir, slug)
    with _locked(state_dir, slug):
        data = _read(path)
        items = [item for item in data.get(role) or [] if isinstance(item, dict)]
        kept = [item for item in items if item.get("pattern") != pattern]
        if len(kept) == len(items):
            return False
        if kept:
            data[role] = kept
        else:
            data.pop(role, None)
        _write(path, data)
    logger.info("allowlist %s/%s: revoked %s", slug, role, pattern)
    return True


def merged_allowed_tools(state_dir: str, slug: str | None, role: str, base: list[str]) -> list[str]:
    """`base`, then the project's patterns for `role` that still pass, with no repeat.

    `base` is the global list (and the cartografo's own, for it) and comes
    first, unchanged. A stored pattern that fails the rules is skipped and
    logged; a file that cannot be read adds nothing and is logged, since a
    phase without its standing grants only files the request again.
    """
    merged = list(dict.fromkeys(base))
    if not slug:
        return merged
    try:
        stored = entries(state_dir, slug, role)[role]
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("allowlist %s/%s: not read, nothing added: %s", slug, role, exc)
        return merged
    for item in stored:
        pattern = item.get("pattern")
        problems = stored_pattern_problems(pattern)
        if problems:
            logger.warning(
                "allowlist %s/%s: skipping %r: %s", slug, role, pattern, "; ".join(problems),
            )
            continue
        if pattern not in merged:
            merged.append(pattern)
    return merged
