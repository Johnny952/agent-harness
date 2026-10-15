import pytest

from dispatcher import surface

# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------


def test_parse_realistic_section_keeps_paths_and_drops_identifiers():
    text = "`dispatcher/config.py` `load_config`, and its tests in `tests/dispatcher/test_config.py`."
    assert surface.parse(text) == {"dispatcher/config.py", "tests/dispatcher/test_config.py"}


def test_parse_prose_with_no_path_is_the_whole_repo():
    entries = surface.parse("The config loader and whatever it touches, `load_config` included.")
    assert entries == surface.WHOLE_REPO
    assert surface.is_whole_repo(entries)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_parse_absent_or_blank_section_is_the_whole_repo(text):
    assert surface.is_whole_repo(surface.parse(text))


def test_parse_keeps_directories_globs_and_dotfiles_and_strips_dot_slash():
    text = "`./tests/dispatcher/`, `docs/plans/*.md`, `.gitattributes` and `config.yaml`."
    assert surface.parse(text) == {
        "tests/dispatcher/",
        "docs/plans/*.md",
        ".gitattributes",
        "config.yaml",
    }


@pytest.mark.parametrize(
    "token",
    [
        "load_config",
        "load_config()",
        "dispatch run-task",
        "merge=union",
        "list[str]",
        "--parallel",
        "/etc/passwd",
        "../outside.py",
        "$HOME/x.py",
    ],
)
def test_parse_ignores_backticked_code(token):
    assert surface.parse(f"`{token}`") == surface.WHOLE_REPO


# --------------------------------------------------------------------------
# covers
# --------------------------------------------------------------------------


def test_a_file_covers_itself_only():
    entries = frozenset({"dispatcher/config.py"})
    assert surface.covers(entries, "dispatcher/config.py")
    assert surface.covers(entries, "./dispatcher/config.py")
    assert not surface.covers(entries, "dispatcher/config.pyc")
    assert not surface.covers(entries, "dispatcher/gates.py")


def test_a_directory_covers_what_is_under_it_by_whole_segments():
    for entries in (frozenset({"tests/dispatcher/"}), frozenset({"tests/dispatcher"})):
        assert surface.covers(entries, "tests/dispatcher/test_config.py")
        assert surface.covers(entries, "tests/dispatcher/deep/x.py")
        assert not surface.covers(entries, "tests/dispatcher_old/x.py")
        assert not surface.covers(entries, "tests/test_x.py")


def test_a_glob_star_stays_in_one_segment():
    entries = frozenset({"docs/plans/*.md"})
    assert surface.covers(entries, "docs/plans/autonomy.md")
    assert not surface.covers(entries, "docs/plans/old/autonomy.md")
    assert not surface.covers(entries, "docs/plans/autonomy.txt")


def test_a_glob_double_star_crosses_segments():
    entries = frozenset({"docker/compose/**"})
    assert surface.covers(entries, "docker/compose/a.yml")
    assert surface.covers(entries, "docker/compose/x/y/a.yml")
    assert not surface.covers(entries, "docker/other.yml")
    assert surface.covers(frozenset({"docs/**/x.md"}), "docs/x.md")
    assert surface.covers(frozenset({".env*"}), ".env.local")


def test_the_whole_repo_covers_everything():
    assert surface.covers(surface.WHOLE_REPO, "anything/at/all.py")


# --------------------------------------------------------------------------
# overlaps
# --------------------------------------------------------------------------


def test_overlapping_surfaces():
    a = frozenset({"dispatcher/config.py", "tests/dispatcher/test_config.py"})
    assert surface.overlaps(a, frozenset({"dispatcher/"}))
    assert surface.overlaps(frozenset({"tests/dispatcher/"}), a)
    assert surface.overlaps(frozenset({"docs/plans/*.md"}), frozenset({"docs/plans/autonomy.md"}))
    assert surface.overlaps(frozenset({"docs/"}), frozenset({"docs/plans/*.md"}))


def test_disjoint_surfaces():
    a = frozenset({"dispatcher/config.py", "tests/dispatcher/test_config.py"})
    b = frozenset({"dispatcher/gates.py", "tests/dispatcher/test_gates.py"})
    assert not surface.overlaps(a, b)
    assert not surface.overlaps(frozenset({"docs/plans/*.md"}), frozenset({"front/"}))
    assert not surface.overlaps(frozenset({"dispatcher/surface.py"}), frozenset({"dispatcher/surface"}))


def test_the_whole_repo_overlaps_everything():
    assert surface.overlaps(surface.WHOLE_REPO, frozenset({"front/x.ts"}))
    assert surface.overlaps(frozenset({"front/x.ts"}), surface.WHOLE_REPO)


def test_an_append_only_path_overlaps_nothing():
    logs = frozenset({"docs/decisions.md", "docs/learnings/x.md"})
    assert not surface.overlaps(logs, logs)
    assert not surface.overlaps(logs, frozenset({"docs/decisions.md", "dispatcher/x.py"}))
    assert not surface.overlaps(frozenset({"docs/ROADMAP.md"}), frozenset({"docs/ROADMAP.md"}))


# --------------------------------------------------------------------------
# outside
# --------------------------------------------------------------------------


def test_outside_allows_append_only_paths_and_names_strays():
    entries = surface.parse("`dispatcher/surface.py`, `tests/dispatcher/test_surface.py`")
    changed = [
        "dispatcher/surface.py",
        "docs/implementations/A-1.md",
        "docs/decisions.md",
        "dispatcher/gates.py",
        "tests/dispatcher/test_surface.py",
    ]
    assert surface.outside(entries, changed) == ["dispatcher/gates.py"]


def test_outside_is_empty_for_the_whole_repo():
    assert surface.outside(surface.WHOLE_REPO, ["a.py", "b/c.py"]) == []
