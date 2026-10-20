"""The project record: `<hive>/projects/<slug>.json` (ADR 199).

`docs/plans/projects.md`, *Decisions* 1 and step 1. A project is a clone
directory under `projects_root` that a record names: its slug (the
directory's name), a title, its remote in SSH form and its base branch, its
deploy key's fingerprint and when it was made (never the key itself), the
settings it overrides `config.yaml` with, when it was created, and whether it
is active or archived.

This module is the one place the record is read, validated and written. The
board service writes it on a person's act (the console's routes are steps 4
and 5 of the plan); the dispatcher only reads it, through `settings_for`, and
`dispatch migrate` writes the first ones through `migrate`. The record lives
in the hive, beside `tasks/`, because the hive is the volume every one of
those three already mounts, and the dispatcher does not mount the projects.

The record is strict: an unknown key is refused rather than ignored, so a
typo in a hand edit is a named error, not a setting that silently does
nothing. A broken record stops the project's cycle instead of falling back to
`config.yaml`, for the same reason: the record says this project's settings
differ, and running it on the defaults would be running it on settings
nobody chose for it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from dispatcher import context_transfer, docker_exec, project_keys, tasks

#: `git@host:owner/repo.git`, the scp-like form, or an `ssh://` URL.
_SCP_REMOTE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+$")
_SSH_URL = re.compile(r"^ssh://[^\s/]+/[^\s]+$")
_HTTPS_REMOTE = re.compile(r"^https?://(?:[^@/\s]+@)?([A-Za-z0-9.-]+)(?::\d+)?/([^\s]+?)/?$")

STATUSES = ("active", "archived")

#: The settings a record may carry, each the `Config` field it overrides, and
#: the check its value is held to. `push_task_branch` has no `Config` field:
#: the task-branch push it governs is not built yet (charter C-5), so it is
#: recorded and validated and read by nothing.
_SETTINGS = {
    "allowed_tools": lambda v: isinstance(v, list) and all(isinstance(t, str) and t.strip() for t in v),
    "merge_on_done": lambda v: isinstance(v, bool),
    "gates_enabled": lambda v: isinstance(v, bool),
    "gates_test_timeout_seconds": lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
    "push_task_branch": lambda v: isinstance(v, bool),
}
_SETTING_WANTS = {
    "allowed_tools": "a list of tool names",
    "merge_on_done": "true or false",
    "gates_enabled": "true or false",
    "gates_test_timeout_seconds": "a whole number of seconds above 0",
    "push_task_branch": "true or false",
}
#: The ones `settings_for` lays over the config.
_CONFIG_SETTINGS = ("allowed_tools", "merge_on_done", "gates_enabled", "gates_test_timeout_seconds")

_FIELDS = ("slug", "title", "remote", "base_branch", "key", "settings", "created", "status")


class RecordError(ValueError):
    """A record that is not one: unreadable, or a field that fails its check."""


class RecordExists(FileExistsError):
    """A record is already there and replacing it was not asked for."""


class DiscoverError(RuntimeError):
    """The clones under `projects_root` could not be listed."""


@dataclasses.dataclass(frozen=True)
class KeyRecord:
    #: `SHA256:...`, as `ssh-keygen -l` prints it.
    fingerprint: str
    #: When the pair was made, ISO 8601.
    created: str


@dataclasses.dataclass(frozen=True)
class ProjectRecord:
    slug: str
    title: str
    remote: str | None
    base_branch: str | None
    key: KeyRecord | None
    #: Only the settings this project overrides; any other falls back to `config.yaml`.
    settings: dict
    created: str
    status: str

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        return {name: data[name] for name in _FIELDS}


def is_slug(value: object) -> bool:
    """One directory name: no separator, no leading dot (the API's `_project_slugs`
    skips dotted directories), nothing a shell or a URL would mangle. The rule a
    card's `project` is held to as well (`tasks.is_project_slug`)."""
    return tasks.is_project_slug(value)


def is_ssh_remote(value: object) -> bool:
    return isinstance(value, str) and bool(_SCP_REMOTE.match(value) or _SSH_URL.match(value))


def ssh_remote(url: str | None) -> str | None:
    """`url` in SSH form, or None when it has no SSH form this can write.

    An SSH remote is kept as it is. An `https://host/owner/repo` one becomes
    `git@host:owner/repo.git`, dropping any credentials in it: the deploy key
    (Decisions 2) authenticates over SSH, and a token in a URL is the thing
    the record must never carry. A local path has no SSH form.
    """
    if not url:
        return None
    url = url.strip()
    if is_ssh_remote(url):
        return url
    match = _HTTPS_REMOTE.match(url)
    if match is None:
        return None
    host, path = match.groups()
    if not path.endswith(".git"):
        path += ".git"
    return f"git@{host}:{path}"


def _is_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        dt.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _problems(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["it is not a JSON object"]
    problems = []
    unknown = sorted(set(data) - set(_FIELDS))
    if unknown:
        problems.append(f"it has fields no record has: {', '.join(unknown)}")
    missing = [name for name in _FIELDS if name not in data]
    if missing:
        problems.append(f"it lacks {', '.join(missing)}")
    if "slug" in data and not is_slug(data["slug"]):
        problems.append(f"its slug {data['slug']!r} is not a directory name")
    if "title" in data and not (isinstance(data["title"], str) and data["title"].strip()):
        problems.append("its title is empty")
    remote = data.get("remote")
    if remote is not None and not is_ssh_remote(remote):
        problems.append(f"its remote {remote!r} is not in SSH form (git@host:owner/repo.git)")
    branch = data.get("base_branch")
    if branch is not None and not (isinstance(branch, str) and branch.strip()):
        problems.append(f"its base_branch {branch!r} is not a branch name")
    key = data.get("key")
    if key is not None:
        if not isinstance(key, dict) or set(key) != {"fingerprint", "created"}:
            problems.append("its key is not {fingerprint, created}: the record never holds a key")
        else:
            if not (isinstance(key["fingerprint"], str) and key["fingerprint"].startswith("SHA256:")):
                problems.append("its key fingerprint is not a SHA256: fingerprint")
            if not _is_time(key["created"]):
                problems.append("its key created is not an ISO 8601 time")
    settings = data.get("settings")
    if "settings" in data:
        if not isinstance(settings, dict):
            problems.append("its settings is not an object")
        else:
            for name, value in settings.items():
                check = _SETTINGS.get(name)
                if check is None:
                    problems.append(f"its settings name {name!r}, which no project setting is")
                elif not check(value):
                    problems.append(f"its {name} is {value!r}, not {_SETTING_WANTS[name]}")
    if "created" in data and not _is_time(data["created"]):
        problems.append("its created is not an ISO 8601 time")
    if "status" in data and data["status"] not in STATUSES:
        problems.append(f"its status {data['status']!r} is not one of {', '.join(STATUSES)}")
    return problems


def parse(data: object) -> ProjectRecord:
    """A record from its JSON object, or `RecordError` naming every problem."""
    problems = _problems(data)
    if problems:
        raise RecordError("; ".join(problems))
    key = data["key"]
    return ProjectRecord(
        slug=data["slug"],
        title=data["title"],
        remote=data["remote"],
        base_branch=data["base_branch"],
        key=None if key is None else KeyRecord(key["fingerprint"], key["created"]),
        settings=dict(data["settings"]),
        created=data["created"],
        status=data["status"],
    )


def projects_dir(hive_root: str) -> str:
    """`<hive root>/projects`, beside `tasks/`. Derived, as `context_transfer.hive_root` is."""
    return os.path.join(hive_root, "projects")


def record_path(hive_root: str, slug: str) -> str:
    if not is_slug(slug):
        raise RecordError(f"{slug!r} is not a project slug")
    return os.path.join(projects_dir(hive_root), f"{slug}.json")


def read(hive_root: str, slug: str) -> ProjectRecord | None:
    """The project's record, None when it has none, `RecordError` when it is broken."""
    path = record_path(hive_root, slug)
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RecordError(f"{path}: unreadable: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RecordError(f"{path}: not JSON: {exc}") from exc
    try:
        record = parse(data)
    except RecordError as exc:
        raise RecordError(f"{path}: {exc}") from exc
    if record.slug != slug:
        raise RecordError(f"{path}: its slug is {record.slug!r}, not its file's name {slug!r}")
    return record


def slugs(hive_root: str) -> list[str]:
    """The slugs that have a record file, sorted, whether or not it reads."""
    try:
        names = os.listdir(projects_dir(hive_root))
    except FileNotFoundError:
        return []
    return sorted(
        name[: -len(".json")] for name in names
        if name.endswith(".json") and is_slug(name[: -len(".json")])
    )


def read_all(hive_root: str) -> list[ProjectRecord]:
    """Every record, by slug. A broken one raises: a list that skipped it would hide it."""
    return [record for slug in slugs(hive_root) if (record := read(hive_root, slug)) is not None]


def write(hive_root: str, record: ProjectRecord, *, replace: bool = False) -> str:
    """Write `record`, refusing one that is already there unless `replace`.

    The one writer. The record is validated again, written to a temporary
    file in the same directory, and linked into place: `os.link` fails when
    the name exists, so two writers racing for a new project cannot both win
    and neither writes over a record a person made. `replace` uses
    `os.replace` instead. 0644 whatever the umask, as the hive's other files,
    so the API's reader of another uid can read it.
    """
    record = parse(record.to_dict())
    path = record_path(hive_root, record.slug)
    directory = projects_dir(hive_root)
    os.makedirs(directory, mode=0o755, exist_ok=True)
    os.chmod(directory, 0o755)
    fd, staged = tempfile.mkstemp(prefix=f".{record.slug}.", suffix=".json.new", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(record.to_dict(), fh, indent=2, sort_keys=False)
            fh.write("\n")
        os.chmod(staged, 0o644)
        if replace:
            os.replace(staged, path)
        else:
            try:
                os.link(staged, path)
            except FileExistsError:
                raise RecordExists(f"{path} already exists") from None
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    return path


def settings_for(cfg, slug: str):
    """`cfg` with `slug`'s record's settings laid over it.

    A setting the record leaves out keeps `config.yaml`'s; a project with no
    record, or a record with no settings, gets `cfg` itself, so a harness with
    one hand-configured project runs exactly as before. Idempotent, so a call
    on a config this already returned changes nothing. `RecordError` for a
    broken record: the caller stops that project's work.
    """
    record = read(context_transfer.hive_root(cfg.hive_tasks_dir), slug)
    if record is None:
        return cfg
    changes = {name: record.settings[name] for name in _CONFIG_SETTINGS if name in record.settings}
    if "allowed_tools" in changes:
        changes["allowed_tools"] = list(changes["allowed_tools"])
    if not changes:
        return cfg
    return dataclasses.replace(cfg, **changes)


# -- the migration ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Clone:
    """A directory under `projects_root`, as `discover` found it."""

    slug: str
    #: `remote.origin.url` as git has it, or None.
    remote: str | None
    #: The branch its main checkout is on, or None when detached or not a repository.
    base_branch: str | None


def discover(container: str, projects_root: str) -> list[Clone]:
    """Every non-dotted directory under `projects_root`, by name.

    Run in an agent container, which mounts the projects; the dispatcher's
    does not. The same rule as the API's `_project_slugs`: a directory is a
    project, a dotted one is not.
    """
    proc = docker_exec.run_docker_exec(
        container, projects_root,
        ["find", projects_root, "-mindepth", "1", "-maxdepth", "1", "-type", "d",
         "-not", "-name", ".*", "-printf", "%f\\n"],
    )
    if proc.returncode != 0:
        raise DiscoverError(f"listing {projects_root} failed: {proc.stderr.strip()}")
    clones = []
    for name in sorted(line for line in proc.stdout.splitlines() if line):
        directory = os.path.join(projects_root, name)
        remote = docker_exec.run_docker_exec(
            container, directory, ["git", "config", "--get", "remote.origin.url"],
            env=docker_exec._GIT_ENV,
        )
        clones.append(Clone(
            slug=name,
            remote=(remote.stdout.strip() or None) if remote.returncode == 0 else None,
            base_branch=docker_exec.current_branch(container, directory),
        ))
    return clones


@dataclasses.dataclass(frozen=True)
class ProjectsReport:
    """What `migrate` did with the records and the cards."""

    #: Slugs whose record this run wrote.
    written: list[str]
    #: Slugs that already had a record, left as they were.
    existing: list[str]
    #: (directory name, reason) for a clone that got no record.
    skipped: list[tuple[str, str]]
    #: Task ids this run stamped with `default`.
    stamped: list[str]
    #: (task id, reason) for a card left unstamped, or ("cards", reason) for all of them.
    refused: list[tuple[str, str]]
    #: Cards that already named a project.
    already: int
    #: The project unstamped cards were given, or None.
    default: str | None


def _now_iso(now: dt.datetime | None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _key_record(keys_dir: str, slug: str) -> KeyRecord | None:
    """The project's deploy key's fingerprint and age, or None.

    Never the key: `project_keys.read` reads the public half only to
    fingerprint it, and only the `SHA256:` token of that line is kept.
    """
    try:
        key = project_keys.read(keys_dir, slug)
    except (OSError, ValueError, subprocess.SubprocessError):
        # A key that cannot be fingerprinted is recorded as none; the console
        # (step 5) shows the key from the file, not from here.
        return None
    if key is None:
        return None
    fingerprint = next((part for part in key.fingerprint.split() if part.startswith("SHA256:")), None)
    if fingerprint is None:
        return None
    public = key.path.with_name(key.path.name + ".pub")
    created = dt.datetime.fromtimestamp(public.stat().st_mtime, dt.timezone.utc)
    return KeyRecord(fingerprint=fingerprint, created=_now_iso(created))


def migrate(
    hive_dir: str,
    clones: list[Clone],
    *,
    keys_dir: str,
    ttl_seconds: int,
    default: str | None = None,
    now: dt.datetime | None = None,
) -> ProjectsReport:
    """Write one record per clone that has none, then stamp the cards that name no project.

    Idempotent and never destructive: a slug that has a record file, readable
    or not, is left exactly as it is, and a card that names a project is not
    rewritten. The records written carry no settings, so `config.yaml` goes on
    deciding everything until a person changes one. The remote is written in
    SSH form, or not at all when it has none.

    The cards get `default`: the one project a harness had before this
    (Decisions 3). Named by `--project`, or, when there is exactly one record
    after the records are written, that one. With several and none named,
    nothing is stamped and the report says so: which project a card belongs
    to is not a thing to guess.
    """
    root = context_transfer.hive_root(hive_dir)
    written: list[str] = []
    existing: list[str] = []
    skipped: list[tuple[str, str]] = []
    have = set(slugs(root))
    for clone in clones:
        if not is_slug(clone.slug):
            skipped.append((clone.slug, "its name is not a project slug"))
            continue
        if clone.slug in have:
            existing.append(clone.slug)
            continue
        record = ProjectRecord(
            slug=clone.slug,
            title=clone.slug,
            remote=ssh_remote(clone.remote),
            base_branch=clone.base_branch,
            key=_key_record(keys_dir, clone.slug),
            settings={},
            created=_now_iso(now),
            status="active",
        )
        try:
            write(root, record)
        except RecordExists:
            existing.append(clone.slug)
            continue
        written.append(clone.slug)
    known = slugs(root)
    refused: list[tuple[str, str]] = []
    if default is None and len(known) == 1:
        default = known[0]
    if default is not None and default not in known:
        refused.append(("cards", f"--project {default} names no project record"))
        default = None
    if default is None:
        unstamped = tasks.unstamped_ids(hive_dir)
        if unstamped and not refused and known:
            refused.append((
                "cards",
                f"{len(unstamped)} card(s) name no project and there are {len(known)} "
                f"projects ({', '.join(known)}); pass --project <slug> to say which one they are",
            ))
        return ProjectsReport(
            sorted(written), sorted(existing), skipped, [], refused,
            tasks.stamped_count(hive_dir), None,
        )
    stamp = tasks.stamp_project(hive_dir, default, ttl_seconds)
    return ProjectsReport(
        sorted(written), sorted(existing), skipped, stamp.stamped, stamp.refused,
        stamp.already, default,
    )


# -- the board service's writes (ADR NNN) ----------------------------------------


class NoRecord(LookupError):
    """A change names a project that has no record."""


class BrokenRecord(RecordError):
    """A change names a record that is on disk but does not read: a person's to fix."""


#: What a person sets on a new record. `key` is `project-keygen`'s (Decisions
#: 2), `created` is the writer's clock and a new record is `active`: none of
#: the three is the body's. Any other name falls to `parse`, which names it.
CREATE_FIELDS = ("slug", "title", "remote", "base_branch", "settings")
#: What a person changes on a record. `slug` is the file's name, so changing it
#: would be another project; `key` and `created` as above.
CHANGE_FIELDS = ("title", "remote", "base_branch", "settings", "status")
_NOT_SETTABLE = {
    "key": "key is project-keygen's: the record holds its fingerprint and never takes one from a request",
    "created": "created is the time the record was written, not a field to set",
}


def _settable(fields: object, allowed: tuple[str, ...], refused: dict[str, str]) -> dict:
    """`fields` as a dict, or `RecordError` for a field this write never takes.

    A name that is neither allowed nor refused here is left in, for `parse` to
    name in the reader's words ("it has fields no record has").
    """
    if not isinstance(fields, dict):
        raise RecordError("it is not a JSON object")
    for name in fields:
        if name in allowed:
            continue
        reason = refused.get(name) or _NOT_SETTABLE.get(name)
        if reason is not None:
            raise RecordError(reason)
    return dict(fields)


def create(hive_root: str, fields: dict, *, now: dt.datetime | None = None) -> ProjectRecord:
    """A new record from a person's `fields`, written, or the reason it is not.

    `slug` is required; `title` defaults to the slug, as `migrate` writes it;
    `remote` and `base_branch` default to none and `settings` to none of them,
    so `config.yaml` decides everything a new record leaves out. The record is
    held to `parse`, the reader's own check, so what this accepts the
    dispatcher reads, and a refusal is in the reader's words. `RecordExists`
    when the slug has a record file, readable or not: `write` links it into
    place, so two creates of one slug cannot both win.
    """
    fields = _settable(fields, CREATE_FIELDS, {
        "status": "a new record is active; status is changed once the record exists",
    })
    slug = fields.get("slug")
    data = {
        "slug": slug,
        "title": slug,
        "remote": None,
        "base_branch": None,
        "key": None,
        "settings": {},
        "created": _now_iso(now),
        "status": "active",
        **fields,
    }
    record = parse(data)
    write(hive_root, record)
    return record


@contextlib.contextmanager
def _record_lock(hive_root: str, slug: str):
    """An exclusive `flock` on `<projects>/.<slug>.lock` for one read-modify-write.

    Dotted and not `.json`, so `slugs` never lists it. Never removed, for the
    reason `tasks.task_lock` gives; 0644 so a reader of another uid can take it.
    """
    directory = projects_dir(hive_root)
    os.makedirs(directory, mode=0o755, exist_ok=True)
    path = os.path.join(directory, f".{slug}.lock")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY)
    else:
        os.fchmod(fd, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def change(hive_root: str, slug: str, fields: dict) -> ProjectRecord:
    """`slug`'s record with `fields` laid over it, written in its place.

    `settings` merges into the record's: a setting named with a value is set,
    one named with null is dropped, so it falls back to `config.yaml` again,
    and one not named is kept. Every other field replaces the record's,
    `status` included (`archived` and back). `NoRecord` when there is none;
    `BrokenRecord` when the record on disk does not read (it is not rewritten
    from a guess); `RecordError` when the result fails `parse`. Read and written under the record's
    lock, so two changes cannot lose one.
    """
    fields = _settable(fields, CHANGE_FIELDS, {
        "slug": "slug is the record's file name; a project is not renamed",
    })
    if not fields:
        raise RecordError("nothing to change")
    if not is_slug(slug):
        raise NoRecord(f"no project record {slug!r}")
    with _record_lock(hive_root, slug):
        try:
            current = read(hive_root, slug)
        except RecordError as exc:
            raise BrokenRecord(str(exc)) from exc
        if current is None:
            raise NoRecord(f"no project record {slug!r}")
        data = current.to_dict()
        if "settings" in fields:
            settings = fields.pop("settings")
            if isinstance(settings, dict):
                merged = dict(current.settings)
                for name, value in settings.items():
                    if value is None:
                        merged.pop(name, None)
                    else:
                        merged[name] = value
                settings = merged
            data["settings"] = settings  # not a dict: `parse` names it
        data.update(fields)
        record = parse(data)
        write(hive_root, record, replace=True)
    return record
