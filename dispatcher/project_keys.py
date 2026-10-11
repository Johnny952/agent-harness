"""Each project's SSH deploy key: made here, on the dispatcher's side.

A deploy key opens one repository and is revoked from that one repository,
where a personal token usually opens every repository of its owner
(docs/plans/projects.md, Decisions 2). The pair is made by `ssh-keygen` into
the credentials directory the dispatcher's container mounts writable and the
agents mount read-only (docker_exec.PROJECT_KEYS_DIR), so the private half
never leaves the dispatcher's side (charter C-5): nothing here reads it, and
nothing here prints it. What comes back is the public half, for the person to
paste into the repository's *Deploy keys*, and its fingerprint.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

from dispatcher import docker_exec


@dataclasses.dataclass(frozen=True)
class DeployKey:
    #: The private half's path. Named, never read.
    path: Path
    #: The `.pub` file's one line, which is safe to show anywhere.
    public: str
    #: `ssh-keygen -l`'s line for it, such as "256 SHA256:... comment (ED25519)".
    fingerprint: str


class KeyExists(Exception):
    """A key is already there and replacing it was not asked for."""


def _check_slug(slug: str) -> None:
    # The slug becomes a directory under the credentials volume, so anything
    # that could step out of it, or hide in it, is refused rather than cleaned.
    if not slug or "/" in slug or "\\" in slug or slug.startswith(".") or "\0" in slug:
        raise ValueError(f"{slug!r} is not a project slug")


def key_path(keys_dir: str | os.PathLike[str], slug: str) -> Path:
    _check_slug(slug)
    return Path(docker_exec.project_key_path(os.fspath(keys_dir), slug))


def fingerprint(public_path: Path) -> str:
    done = subprocess.run(
        ["ssh-keygen", "-l", "-f", str(public_path)],
        capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def read(keys_dir: str | os.PathLike[str], slug: str) -> DeployKey | None:
    """The project's key as it is, or None when it has none."""
    path = key_path(keys_dir, slug)
    public_path = path.with_name(path.name + ".pub")
    if not path.is_file() or not public_path.is_file():
        return None
    return DeployKey(path, public_path.read_text().strip(), fingerprint(public_path))


def generate(keys_dir: str | os.PathLike[str], slug: str, *, replace: bool = False) -> DeployKey:
    """Make an ed25519 pair for `slug`, and return its public half.

    Refuses a project that already has a key unless `replace`: a new pair is a
    new public key to paste on GitHub, and the old one stops working the moment
    it is overwritten. The pair is made under a temporary name and moved into
    place, so a failed `ssh-keygen` leaves the old key, or none, never half of
    one. No passphrase, because nobody is there to type it when a phase fetches.
    """
    path = key_path(keys_dir, slug)
    if path.exists() and not replace:
        raise KeyExists(f"{path} already exists; --replace makes a new one")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    staged = path.with_name(path.name + ".new")
    for leftover in (staged, staged.with_name(staged.name + ".pub")):
        leftover.unlink(missing_ok=True)
    subprocess.run(
        [
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
            "-C", f"ia-harness:{slug}", "-f", str(staged),
        ],
        capture_output=True, text=True, check=True,
    )
    public_path = path.with_name(path.name + ".pub")
    os.replace(staged, path)
    os.replace(staged.with_name(staged.name + ".pub"), public_path)
    return DeployKey(path, public_path.read_text().strip(), fingerprint(public_path))
