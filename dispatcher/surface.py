# dispatcher/surface.py
"""A task's *Surface* section, read as the repo paths it names.

`docs/plans/autonomy.md` *Decisions*, 5: two decisions read a task's surface
as paths — the automatic approve (every changed path must be inside it) and
parallel work (two tasks whose surfaces overlap never write at once). The
section stays free prose for the roles (`tasks.Description.surface`); this
module only picks the paths out of it.

A surface is a frozenset of entries, and the empty frozenset (`WHOLE_REPO`)
means the whole repo: a section with no path in it, or no section at all. It
covers every path, overlaps every surface, and A-3 never approves it
automatically. The convention is an empty set rather than a sentinel so a
surface stays a plain value that sorts, compares and serializes.

An entry is a repo-relative path or glob, normalized: a leading `./` is
dropped, and a trailing `/` is kept to say "a directory". A literal entry
covers itself and everything under it whether or not it ends in `/` — a
surface naming `tests/dispatcher` means the directory as surely as one naming
`tests/dispatcher/`. A glob entry matches with `*` and `?` staying inside one
path segment and `**` crossing any number of them (`docs/**` is everything
under `docs/`, `docs/*.md` only its top level), the shell's and git's
semantics rather than `fnmatch`'s, whose `*` crosses `/`.

Where it cannot tell, this module errs toward overlap: a false overlap costs
two tasks running one after the other, a missed one costs a merge conflict.
"""
from __future__ import annotations

import re

#: The surface that names no path: the whole repo.
WHOLE_REPO: frozenset[str] = frozenset()

#: The append-only logs every task may write to (*Decisions*, 5). They never
#: cause an overlap, and a change to them is never outside a surface. The
#: plan names only the *Results log* of `docs/ROADMAP.md`; a path cannot name
#: a section, so the whole file is shared here.
APPEND_ONLY: tuple[str, ...] = (
    "docs/decisions.md",
    "docs/ROADMAP.md",
    "docs/implementations/",
    "docs/learnings/",
)

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_GLOB_CHARS = frozenset("*?[")
#: Characters no repo path in a surface would carry, and code or a command
#: often does: `load_config()`, `merge=union`, `a, b`, `$HOME`, `x | y`.
_NOT_A_PATH = frozenset(" \t()=,;:'\"<>|$&{}")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]+$")


def _looks_like_path(token: str) -> bool:
    """Whether one backticked token is a repo path or glob rather than code.

    A token is a path when it has none of `_NOT_A_PATH`, does not start with
    `-` (a flag), `/` (an absolute path, not the repo's) or `~`, has no `..`
    segment, and has at least one of: a `/`, a `*` or `?`, or an extension on
    its last segment (`config.yaml`, `.gitattributes`). So `load_config`,
    `merge_on_done`, `list[str]` and `dispatch run-task` are not paths. A dotted name with
    an alphanumeric tail (`os.path`) is taken as one; a surface with a stray
    entry for a file that does not exist only widens it by nothing.
    """
    if not token or any(char in _NOT_A_PATH for char in token):
        return False
    if token[0] in "-/~" or ".." in token.split("/"):
        return False
    if "/" in token or "*" in token or "?" in token:
        return True
    return bool(_EXTENSION.search(token))


def _normalize(path: str) -> str:
    """A path with its leading `./` dropped; a trailing `/` is kept."""
    while path.startswith("./"):
        path = path[2:]
    return path


def parse(text: str | None) -> frozenset[str]:
    """The path entries of a *Surface* section; `WHOLE_REPO` when it has none.

    Each backtick-quoted token that `_looks_like_path` is one entry, and
    everything else in the section — prose, identifiers, commands — is
    ignored. `None` (no section) is the whole repo, as an all-prose one is.
    """
    if not text:
        return WHOLE_REPO
    entries = (_normalize(match.group(1).strip()) for match in _BACKTICKED.finditer(text))
    return frozenset(entry for entry in entries if entry and _looks_like_path(entry))


def is_whole_repo(entries: frozenset[str]) -> bool:
    """Whether a surface names no path, and so means the whole repo."""
    return not entries


def _is_glob(entry: str) -> bool:
    return bool(_GLOB_CHARS & set(entry))


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """`pattern` as a regex: `*` and `?` within a segment, `**` across them.

    `**/` also matches no directory at all, so `docs/**/x.md` matches
    `docs/x.md`. A trailing `/` on the pattern is dropped: a glob for a
    directory matches the directory's own path.
    """
    pattern = pattern.rstrip("/")
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 2 :]:
            end = pattern.index("]", i + 2)
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _static_prefix(glob: str) -> str:
    """The segments of a glob before its first glob character, as a literal."""
    segments: list[str] = []
    for segment in glob.split("/"):
        if _is_glob(segment):
            break
        segments.append(segment)
    return "/".join(segments)


def _under(path: str, entry: str) -> bool:
    """Whether a literal entry is `path` itself or a directory holding it.

    Compared by whole segments, so `docs/plan` does not hold `docs/plans.md`.
    The empty literal (a glob with no static prefix, `*.md`) holds everything.
    """
    root = entry.rstrip("/")
    if not root:
        return True
    path = path.rstrip("/")
    return path == root or path.startswith(root + "/")


def _entry_covers(entry: str, path: str) -> bool:
    if _is_glob(entry):
        return bool(_glob_regex(entry).match(path.rstrip("/")))
    return _under(path, entry)


def covers(entries: frozenset[str], path: str) -> bool:
    """Whether a surface holds `path`: a literal entry or a glob reaches it.

    The whole repo covers everything. Shared append-only paths get no special
    treatment here; `outside` is where they are always allowed.
    """
    if is_whole_repo(entries):
        return True
    path = _normalize(path)
    return any(_entry_covers(entry, path) for entry in entries)


def is_append_only(path: str) -> bool:
    """Whether `path` is one of `APPEND_ONLY`, or under one of its directories."""
    path = _normalize(path)
    return any(_under(path, shared) for shared in APPEND_ONLY)


def _entries_overlap(a: str, b: str) -> bool:
    """Whether two entries can name a common path, erring toward yes.

    Two literals overlap when one holds the other. A glob and a literal
    overlap when the glob matches the literal, or when the literal and the
    glob's static prefix hold one another (`docs/` and `docs/plans/*.md`,
    `docs/plans/x.py` and `docs/*.md`: the second is a false overlap, the
    cost of not solving glob intersection). Two globs, likewise by prefix.
    """
    a_glob, b_glob = _is_glob(a), _is_glob(b)
    if not a_glob and not b_glob:
        return _under(a, b) or _under(b, a)
    if a_glob and not b_glob:
        a, b = b, a
        a_glob, b_glob = b_glob, a_glob
    # Now b is a glob.
    if not a_glob and _entry_covers(b, a):
        return True
    a_root = _static_prefix(a) if a_glob else a
    b_root = _static_prefix(b)
    return _under(a_root, b_root) or _under(b_root, a_root)


def overlaps(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether two surfaces could touch a common path (*Decisions*, 6).

    The whole repo overlaps every surface. Otherwise entries in `APPEND_ONLY`
    are set aside first, so a surface naming only those overlaps nothing.
    """
    if is_whole_repo(a) or is_whole_repo(b):
        return True
    a_own = [entry for entry in a if not is_append_only(entry)]
    b_own = [entry for entry in b if not is_append_only(entry)]
    return any(_entries_overlap(x, y) for x in a_own for y in b_own)


def outside(entries: frozenset[str], changed_paths: list[str]) -> list[str]:
    """The changed paths the surface does not cover, in their given order.

    A change to an `APPEND_ONLY` path is never outside: every task may append
    to the logs without declaring them. The whole repo leaves nothing outside;
    the automatic approve refuses it on its own condition (*Decisions*, 1).
    """
    return [
        path
        for path in changed_paths
        if not is_append_only(path) and not covers(entries, path)
    ]
