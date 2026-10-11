import shutil
import stat
from pathlib import Path

import pytest

from dispatcher import project_keys

needs_ssh_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="no ssh-keygen")


@needs_ssh_keygen
def test_generate_makes_an_ed25519_pair_where_the_agents_look(tmp_path: Path) -> None:
    key = project_keys.generate(tmp_path, "myproj")

    assert key.path == tmp_path / "myproj" / "id_ed25519"
    assert key.path.is_file() and key.path.with_name("id_ed25519.pub").is_file()
    assert key.public.startswith("ssh-ed25519 ") and key.public.endswith(" ia-harness:myproj")
    assert "SHA256:" in key.fingerprint and "(ED25519)" in key.fingerprint
    assert "PRIVATE KEY" not in key.public


@needs_ssh_keygen
def test_generate_keeps_the_private_half_to_its_owner(tmp_path: Path) -> None:
    key = project_keys.generate(tmp_path, "myproj")

    assert stat.S_IMODE(key.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.path.parent.stat().st_mode) == 0o700


@needs_ssh_keygen
def test_generate_refuses_to_overwrite_a_key_unless_asked(tmp_path: Path) -> None:
    first = project_keys.generate(tmp_path, "myproj")

    with pytest.raises(project_keys.KeyExists):
        project_keys.generate(tmp_path, "myproj")
    assert project_keys.read(tmp_path, "myproj").public == first.public

    second = project_keys.generate(tmp_path, "myproj", replace=True)
    assert second.public != first.public
    assert not list((tmp_path / "myproj").glob("*.new*"))


@needs_ssh_keygen
def test_read_returns_the_key_or_none(tmp_path: Path) -> None:
    assert project_keys.read(tmp_path, "myproj") is None
    made = project_keys.generate(tmp_path, "myproj")

    assert project_keys.read(tmp_path, "myproj") == made


@pytest.mark.parametrize("slug", ["", "../x", "a/b", ".hidden", "a\\b"])
def test_a_slug_that_could_leave_the_keys_dir_is_refused(tmp_path: Path, slug: str) -> None:
    with pytest.raises(ValueError):
        project_keys.generate(tmp_path, slug)
    assert not list(tmp_path.iterdir())
