from pathlib import Path

import yaml

from dispatcher import learnings

_SYMPTOM = "ECONNREFUSED 127.0.0.1:5432 (pid 4711)"


def _entry(
    hive: Path,
    name: str,
    *,
    where: str = learnings.INBOX_NAME,
    symptom: str = _SYMPTOM,
    rule: str = "Start postgres before the suite, not with it.",
    **meta,
) -> Path:
    """One entry file, in the shape the phases' prompt tells them to write.

    Written as text rather than through the module so the tests read the same
    files an agent produces, frontmatter and headings included.
    """
    frontmatter = {
        "project": "myproj",
        "task": "task-1",
        "phase": "implementador",
        "scope": learnings.SCOPE_PROJECT,
        "status": learnings.UNCONFIRMED,
        "when": "the suite talks to a database",
    }
    frontmatter.update(meta)
    path = Path(learnings.ensure_dirs(str(hive))) / where / name
    path.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False)
        + "---\n\n"
        f"## Symptom\n\n```\n{symptom}\n```\n\n"
        "## Why\n\nThe fixture assumed a server that nothing starts.\n\n"
        f"## Rule\n\n{rule}\n\n"
        "## Evidence\n\n`pytest -q tests/db` in the writers' worktree.\n"
    )
    return path


def _card(hive: Path, task_id: str, *, body: str = "## auditor\n\nFiled.\n") -> Path:
    """The task card, which is where the drop looks for its evidence.

    A `## <role>` section is the dispatcher's own record that the role's phase
    returned, and `drop_promoted` moves nothing without the promoting role's
    (`docs/decisions.md` ADR 34). The frontmatter is the two keys
    `read_task_file` insists on and nothing else.
    """
    hive.mkdir(parents=True, exist_ok=True)
    path = hive / f"{task_id}.md"
    path.write_text(
        "---\n"
        + yaml.safe_dump({"task_id": task_id, "status": "done"}, sort_keys=False)
        + "---\n\n"
        + body
    )
    return path


def _meta(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---")[1])


def _row(ref: str, **meta) -> learnings.Entry:
    return learnings.Entry(path="", ref=ref, meta=meta, body="")


def test_the_learnings_root_sits_beside_the_task_files() -> None:
    """Derived from hive_tasks_dir instead of configured, so the path a prompt
    quotes is the same path on the dispatcher and in both containers. A
    trailing slash is what a hand-written config.yaml most often has."""
    assert learnings.root_dir("/data/.hive/tasks") == "/data/.hive/learnings"
    assert learnings.root_dir("/data/.hive/tasks/") == "/data/.hive/learnings"
    assert learnings.inbox_dir("/data/.hive/tasks") == "/data/.hive/learnings/inbox"
    assert learnings.harness_dir("/data/.hive/tasks") == "/data/.hive/learnings/harness"


def test_ensure_dirs_makes_both_halves_before_a_phase_needs_them(tmp_path: Path) -> None:
    """A phase about to run out of turns will not spend one on mkdir -p."""
    hive = tmp_path / "hive"

    root = learnings.ensure_dirs(str(hive))

    assert Path(root).name == learnings.ROOT_NAME
    assert Path(learnings.inbox_dir(str(hive))).is_dir()
    assert Path(learnings.harness_dir(str(hive))).is_dir()
    learnings.ensure_dirs(str(hive))  # again: a second task must not blow up


def test_an_entry_carries_its_rule_and_its_trigger_into_the_table(tmp_path: Path) -> None:
    hive = tmp_path / "hive"
    _entry(hive, "task-1-db.md")

    (entry,) = learnings.read_inbox(str(hive))

    assert entry.ref == "inbox/task-1-db.md"
    assert entry.project == "myproj"
    assert entry.task == "task-1"
    assert entry.scope == learnings.SCOPE_PROJECT
    assert entry.status == learnings.UNCONFIRMED
    assert entry.when == "the suite talks to a database"
    assert entry.rule == "Start postgres before the suite, not with it."
    assert entry.reviewed is False


def test_a_file_without_frontmatter_is_left_where_it_is(tmp_path: Path) -> None:
    """A phase that wrote a note in its own shape still wrote it on purpose,
    and the grep the agents are told to run finds it. What the dispatcher
    must not do is rewrite or delete it."""
    hive = tmp_path / "hive"
    stray = Path(learnings.ensure_dirs(str(hive))) / learnings.INBOX_NAME / "note.md"
    stray.write_text("no frontmatter, just prose\n")

    assert learnings.read_inbox(str(hive)) == []
    assert stray.read_text() == "no frontmatter, just prose\n"


def test_the_fingerprint_ignores_what_changes_between_two_runs(tmp_path: Path) -> None:
    """Pids, timings and addresses differ every run; if they counted, no two
    tasks would ever be seen to have hit the same wall."""
    hive = tmp_path / "hive"
    _entry(hive, "a.md", symptom=_SYMPTOM)
    _entry(hive, "b.md", symptom="ECONNREFUSED 127.0.0.1:5432 (pid 90210)")
    _entry(hive, "c.md", symptom="ENOSPC: no space left on device")

    first, second, third = learnings.read_inbox(str(hive))

    assert first.fingerprint == second.fingerprint
    assert third.fingerprint != first.fingerprint


def test_reconcile_confirms_an_entry_a_second_task_also_hit(tmp_path: Path) -> None:
    """The poisoning guard, and the reason it needs no model: two distinct
    tasks reporting the same error is checkable arithmetic."""
    hive = tmp_path / "hive"
    mine = _entry(hive, "task-1-db.md", task="task-1")
    theirs = _entry(hive, "task-2-db.md", task="task-2", symptom="ECONNREFUSED 127.0.0.1:5432 (pid 8)")
    alone = _entry(hive, "task-3-disk.md", task="task-3", symptom="ENOSPC: no space left on device")

    confirmed = learnings.reconcile(str(hive))

    assert sorted(confirmed) == ["inbox/task-1-db.md", "inbox/task-2-db.md"]
    assert _meta(mine)["status"] == learnings.CONFIRMED
    assert _meta(theirs)["status"] == learnings.CONFIRMED
    assert _meta(alone)["status"] == learnings.UNCONFIRMED
    assert learnings.reconcile(str(hive)) == []  # nothing new to say on a second pass


def test_reconcile_does_not_let_one_task_corroborate_itself(tmp_path: Path) -> None:
    """Two files are two files; the guard is about two independent sightings.
    A promoted entry keeps its original task id, so its own copy cannot back
    it either."""
    hive = tmp_path / "hive"
    twice = _entry(hive, "task-1-db.md", task="task-1")
    again = _entry(hive, "task-1-db-again.md", task="task-1")
    _entry(hive, "shared.md", where=learnings.HARNESS_NAME, task="task-1", scope=learnings.SCOPE_HARNESS)

    assert learnings.reconcile(str(hive)) == []
    assert _meta(twice)["status"] == learnings.UNCONFIRMED
    assert _meta(again)["status"] == learnings.UNCONFIRMED


def test_carry_stamps_the_entries_this_task_is_about_to_file(tmp_path: Path) -> None:
    """The auditor files into one project's docs, so it is handed this
    project's project-scoped entries and nothing else: another project's are
    not its business and a harness-scoped one does not belong in any single
    project's docs."""
    hive = tmp_path / "hive"
    ours = _entry(hive, "ours.md", project="myproj")
    shared = _entry(hive, "shared.md", project="myproj", scope=learnings.SCOPE_HARNESS)
    other = _entry(hive, "other.md", project="otherproj")
    reviewed = _entry(hive, "reviewed.md", where=learnings.HARNESS_NAME, scope=learnings.SCOPE_HARNESS)

    carried = learnings.carry(str(hive), "myproj", "task-9")

    assert carried == ["inbox/ours.md"]
    assert _meta(ours)["carried_by"] == "task-9"
    assert "carried_by" not in _meta(shared)
    assert "carried_by" not in _meta(other)
    assert "carried_by" not in _meta(reviewed)
    assert learnings.carry(str(hive), "myproj", "task-9") == []


def test_carry_takes_over_an_entry_whose_carrier_died(tmp_path: Path) -> None:
    """The dead task is not around to release its claim, so the stamp is
    overwritten rather than respected — otherwise the first task to crash
    holding an entry would strand it forever."""
    hive = tmp_path / "hive"
    stranded = _entry(hive, "stranded.md", carried_by="task-8")

    assert learnings.carry(str(hive), "myproj", "task-9") == ["inbox/stranded.md"]
    assert _meta(stranded)["carried_by"] == "task-9"


def test_drop_promoted_moves_only_what_the_merged_branch_filed(tmp_path: Path) -> None:
    """Once the branch lands, the entry is in the project's docs; keeping the
    inbox copy would charge every later phase for a row the repo already has.
    It moves to `dropped/` rather than being unlinked — no reader walks that
    directory, so the prompt cost is gone and the file is still there for a
    `merge-task` whose cycle never reached an auditor. Everyone else's entries
    are untouched, because their branch has not landed. The card's `## auditor`
    section is what admits the drop at all."""
    hive = tmp_path / "hive"
    _card(hive, "task-9")
    written = _entry(hive, "written.md", task="task-9")
    carried = _entry(hive, "carried.md", task="task-1", carried_by="task-9")
    shared = _entry(hive, "shared.md", task="task-9", scope=learnings.SCOPE_HARNESS)
    someone_else = _entry(hive, "theirs.md", task="task-2")

    dropped = learnings.drop_promoted(str(hive), "task-9")

    assert sorted(dropped) == ["inbox/carried.md", "inbox/written.md"]
    assert not written.exists()
    assert not carried.exists()
    assert shared.exists()
    assert someone_else.exists()

    kept = Path(learnings.dropped_dir(str(hive)))
    assert sorted(path.name for path in kept.iterdir()) == ["carried.md", "written.md"]
    # Moved, not rewritten: what the phase wrote is what a human recovers.
    assert _SYMPTOM in (kept / "written.md").read_text()
    # And invisible to every prompt, which is the whole reason the drop exists.
    assert [entry.ref for entry in learnings.read_all(str(hive))] == [
        "inbox/shared.md", "inbox/theirs.md",
    ]


def test_drop_promoted_does_not_overwrite_an_earlier_dropped_entry(tmp_path: Path) -> None:
    """Two tasks may file the same filename. The second one landing has to
    stand beside the first, not on top of it — an overwrite here is the
    permanent loss `dropped/` exists to stop. Both cards carry the auditor's
    section, which is what admits either drop."""
    hive = tmp_path / "hive"
    _card(hive, "task-9")
    _card(hive, "task-2")
    _entry(hive, "trap.md", task="task-9", symptom="the first one")
    learnings.drop_promoted(str(hive), "task-9")
    _entry(hive, "trap.md", task="task-2", symptom="the second one")

    assert learnings.drop_promoted(str(hive), "task-2") == ["inbox/trap.md"]

    kept = Path(learnings.dropped_dir(str(hive)))
    assert sorted(path.name for path in kept.iterdir()) == ["trap-2.md", "trap.md"]
    assert "the first one" in (kept / "trap.md").read_text()
    assert "the second one" in (kept / "trap-2.md").read_text()


def test_drop_promoted_keeps_what_a_cycle_without_an_auditor_filed(tmp_path: Path) -> None:
    """The case that fired on T-012: a branch landed by hand is one the cycle
    did not land, so its auditor may never have run and nothing was promoted
    into the project's docs. With no `## auditor` section in the card there is
    no evidence of a promotion, so nothing moves — not even into `dropped/`,
    which is not created at all — and the entries stay where the next task
    reading the inbox still sees them."""
    hive = tmp_path / "hive"
    _card(hive, "task-9", body="## implementador\n\nBuilt it.\n\n## revisor\n\nApproved.\n")
    written = _entry(hive, "written.md", task="task-9")
    carried = _entry(hive, "carried.md", task="task-1", carried_by="task-9")

    assert learnings.drop_promoted(str(hive), "task-9") == []

    assert written.exists()
    assert carried.exists()
    assert not Path(learnings.dropped_dir(str(hive))).exists()
    assert [entry.ref for entry in learnings.read_all(str(hive))] == [
        "inbox/carried.md", "inbox/written.md",
    ]


def test_drop_promoted_accepts_the_round_label_a_hand_resumed_auditor_leaves(
    tmp_path: Path,
) -> None:
    """`run-phase --round` labels the section `auditor (round 2)`, because the
    phase loop builds `f"{role} (round {round_num})"` whenever a round number
    is in play. That is the same evidence under a different label, and a
    hand-resumed cycle is exactly the one being landed by hand."""
    hive = tmp_path / "hive"
    _card(hive, "task-9", body="## auditor (round 2)\n\nFiled on the second pass.\n")
    _entry(hive, "written.md", task="task-9")

    assert learnings.drop_promoted(str(hive), "task-9") == ["inbox/written.md"]


def test_drop_promoted_keeps_the_entries_when_the_card_cannot_be_read(tmp_path: Path) -> None:
    """A card that is missing and a card that will not parse are both absence
    of evidence, not evidence of absence, and every unknown here resolves
    towards keeping the entries (ADR 34). None of them raises: a damaged card
    must not turn a `merge-task` into a crash, and a hand-edited card is the
    normal state of the cycles `merge-task` is used on.

    The shapes here are four of the five `read_task_file` raises on: no file at
    all (`OSError`), no `---` delimiter to split on (`ValueError`), frontmatter
    that scans into something that is not a mapping (`TypeError`, twice — a
    scalar and a sequence), and frontmatter that does not scan
    (`yaml.YAMLError`). The fifth, a mapping missing `task_id` (`KeyError`), is
    in `tests/dispatcher/test_context_transfer.py` with the reader's own cases.
    The non-mapping pair is the one
    `docs/learnings/a-never-500-read-wraps-the-use-not-the-parse.md` names and
    the one a four-exception tuple lets through as a crashed `merge-task`.
    """
    hive = tmp_path / "hive"
    missing = _entry(hive, "missing-card.md", task="task-9")

    assert learnings.drop_promoted(str(hive), "task-9") == []
    assert missing.exists()

    hive.mkdir(parents=True, exist_ok=True)
    card = hive / "task-9.md"
    for frontmatter in (
        # No delimiter to split on at all.
        None,
        # Scans, but `fm["task_id"]` subscripts a `str` and then a `list`.
        "TODO write this up",
        "- one bullet\n- another",
        # Does not scan.
        "status: [unclosed",
    ):
        if frontmatter is None:
            card.write_text("## auditor\n\nno frontmatter anywhere\n")
        else:
            card.write_text(f"---\n{frontmatter}\n---\n\n## auditor\n\nFiled.\n")

        assert learnings.drop_promoted(str(hive), "task-9") == []
        assert missing.exists()
        assert not Path(learnings.dropped_dir(str(hive))).exists()


def test_drop_promoted_reads_the_heading_and_not_the_role_name(tmp_path: Path) -> None:
    """The test is the section, not the word. A role writes prose into its
    `**Detail**` and that prose lands inside somebody's section, so a substring
    search for `auditor` would read a sentence saying the auditor never ran as
    proof that it did."""
    hive = tmp_path / "hive"
    _card(
        hive,
        "task-9",
        body=(
            "## implementador\n\n"
            "**Risks**\n- the auditor never ran, so nothing was filed\n\n"
            "### auditor\n\nnot a phase section: three hashes, and nobody renders this\n"
        ),
    )
    written = _entry(hive, "written.md", task="task-9")

    assert learnings.drop_promoted(str(hive), "task-9") == []
    assert written.exists()


def test_droppable_is_exactly_what_the_drop_moves(tmp_path: Path) -> None:
    """One selector behind both, so the count `merge-task` says it kept can
    never disagree with the count it would have moved. Asserted against a card
    that admits the drop, which is the only state in which the two lists can be
    compared at all."""
    hive = tmp_path / "hive"
    _card(hive, "task-9")
    _entry(hive, "written.md", task="task-9")
    _entry(hive, "carried.md", task="task-1", carried_by="task-9")
    _entry(hive, "shared.md", task="task-9", scope=learnings.SCOPE_HARNESS)
    _entry(hive, "theirs.md", task="task-2")

    selected = [entry.ref for entry in learnings.droppable(str(hive), "task-9")]

    assert sorted(selected) == sorted(learnings.drop_promoted(str(hive), "task-9"))
    assert sorted(selected) == ["inbox/carried.md", "inbox/written.md"]


def test_mark_orphaned_releases_the_carrier_and_leaves_a_breadcrumb(tmp_path: Path) -> None:
    """A task that ended blocked filed nothing, so the entries stay — but they
    stop claiming a carrier that is gone, and the next task can see the entry
    has been waiting through more than one of them."""
    hive = tmp_path / "hive"
    entry = _entry(hive, "carried.md", task="task-1", carried_by="task-9",
                   orphaned_from=["t1", "t2", "t3", "t4", "t5"])

    assert learnings.mark_orphaned(str(hive), "task-9") == ["inbox/carried.md"]

    meta = _meta(entry)
    assert "carried_by" not in meta
    assert meta["orphaned_from"] == ["t2", "t3", "t4", "t5", "task-9"]


def test_mark_orphaned_takes_back_a_confirmation_only_this_task_vouched_for(tmp_path: Path) -> None:
    hive = tmp_path / "hive"
    entry = _entry(hive, "mine.md", task="task-9", status=learnings.CONFIRMED)

    assert learnings.mark_orphaned(str(hive), "task-9") == ["inbox/mine.md"]
    assert _meta(entry)["status"] == learnings.UNCONFIRMED


def test_mark_orphaned_keeps_a_confirmation_another_task_backed(tmp_path: Path) -> None:
    """That second sighting did not die with this task, so the confirmation
    it earned is not this task's to take back."""
    hive = tmp_path / "hive"
    mine = _entry(hive, "mine.md", task="task-9", status=learnings.CONFIRMED)
    _entry(hive, "theirs.md", task="task-2", status=learnings.CONFIRMED)

    learnings.mark_orphaned(str(hive), "task-9")

    assert _meta(mine)["status"] == learnings.CONFIRMED


def test_promote_is_the_only_door_into_the_shared_store(tmp_path: Path) -> None:
    """No role and no dispatcher path calls this: it is one phase's word about
    every project at once."""
    hive = tmp_path / "hive"
    original = _entry(hive, "worktree.md", scope=learnings.SCOPE_HARNESS, carried_by="task-9")

    entry = learnings.promote(str(hive), "worktree")

    assert entry is not None
    assert entry.ref == "harness/worktree.md"
    assert entry.reviewed is True
    assert entry.status == learnings.CONFIRMED
    assert entry.scope == learnings.SCOPE_HARNESS
    assert entry.carried_by == ""
    assert entry.rule == "Start postgres before the suite, not with it."
    assert not original.exists()
    assert learnings.promote(str(hive), "worktree") is None  # already through review


def test_a_human_rules_on_an_entry_by_the_ref_the_table_shows(tmp_path: Path) -> None:
    hive = tmp_path / "hive"
    path = _entry(hive, "db.md")

    assert learnings.resolve(str(hive), "db") is not None
    assert learnings.resolve(str(hive), "inbox/db.md") is not None
    assert learnings.resolve(str(hive), "nope") is None

    assert learnings.set_status(str(hive), "db", learnings.CONFIRMED) is not None
    assert _meta(path)["status"] == learnings.CONFIRMED
    assert learnings.set_status(str(hive), "nope", learnings.CONFIRMED) is None

    assert learnings.delete(str(hive), "inbox/db.md") == "inbox/db.md"
    assert not path.exists()
    assert learnings.delete(str(hive), "db") is None


def test_another_projects_unreviewed_entry_is_held_back(tmp_path: Path) -> None:
    """The whole difference the human review makes: before it, an entry is one
    project's claim about its own code."""
    rows = [
        _row("inbox/ours.md", project="myproj"),
        _row("inbox/theirs.md", project="otherproj"),
        _row("harness/reviewed.md", project="otherproj"),
    ]

    assert [entry.ref for entry in learnings.applicable(rows, "myproj")] == [
        "inbox/ours.md",
        "harness/reviewed.md",
    ]


def test_the_table_puts_confirmed_rows_first_and_stops_at_the_cap() -> None:
    """A prompt that grows with the inbox taxes every phase of every task, so
    the table is capped — and what survives the cut is what a second task has
    already backed."""
    rows = [_row(f"inbox/{index:02d}.md", status=learnings.UNCONFIRMED) for index in range(learnings.MAX_ROWS)]
    rows.append(_row("inbox/zz.md", status=learnings.CONFIRMED))

    text = learnings.table(rows)

    assert text.splitlines()[0] == "| # | Learning | When it applies | Status |"
    assert text.splitlines()[2].startswith("| `inbox/zz.md` |")
    assert "`inbox/39.md`" not in text
    assert "1 more are in the directory but not in this table" in text


def test_a_pipe_in_the_text_does_not_break_the_row() -> None:
    """The rule is quoted from a shell command often enough that this is not
    hypothetical."""
    row = _row("inbox/a.md", when="grep -a foo | head")

    assert "grep -a foo \\| head" in learnings.table([row])


def test_duties_says_nothing_to_a_role_that_never_touches_the_code(tmp_path: Path) -> None:
    """A role added elsewhere degrades to silence rather than to an error in
    the middle of a dispatch."""
    assert learnings.duties("recepcionista", "task-1", "myproj", str(tmp_path / "hive")) == ""


def test_every_working_role_is_told_to_grep_before_it_debugs(tmp_path: Path) -> None:
    hive = tmp_path / "hive"
    learnings.ensure_dirs(str(hive))

    text = learnings.duties("implementador", "task-9", "myproj", str(hive))

    assert learnings.root_dir(str(hive)) in text
    assert "grep" in text
    assert f"{learnings.inbox_dir(str(hive))}/task-9-<short-slug>.md" in text
    assert "task: task-9" in text
    assert "phase: implementador" in text
    assert "project: myproj" in text
    assert "status: unconfirmed" in text
    # No entries yet: the format and the grep still ship, the empty table does not.
    assert "| # | Learning |" not in text


def test_only_the_auditor_is_told_which_entries_it_owns(tmp_path: Path) -> None:
    """Every role reads the table; one role files it. Telling the others they
    own entries would have four phases writing the same docs."""
    hive = tmp_path / "hive"
    _entry(hive, "db.md")

    auditor = learnings.duties("auditor", "task-9", "myproj", str(hive))
    implementador = learnings.duties("implementador", "task-9", "myproj", str(hive))

    assert "| `inbox/db.md` |" in auditor
    assert "| `inbox/db.md` |" in implementador
    assert "carried_by: task-9" in auditor
    assert "carried_by" not in implementador


def test_the_harness_fingerprint_covers_the_permissions_and_nothing_else() -> None:
    """The surface that decides whether a trap is real: what a phase may run,
    and whether its edits reach the branch. Order inside the lists is the
    config file's business, not a different harness."""
    base = learnings.harness_fingerprint("acceptEdits", ["Bash(git:*)", "Read"], {"revisor", "auditor"})

    assert base == learnings.harness_fingerprint("acceptEdits", ["Read", "Bash(git:*)"], {"auditor", "revisor"})
    assert base != learnings.harness_fingerprint("auto", ["Bash(git:*)", "Read"], {"revisor", "auditor"})
    assert base != learnings.harness_fingerprint("acceptEdits", [], {"revisor", "auditor"})
    assert base != learnings.harness_fingerprint("acceptEdits", ["Bash(git:*)", "Read"], {"revisor"})


def test_an_entry_written_before_the_stamp_existed_is_unknown_not_stale(tmp_path: Path) -> None:
    """Treating unknown as stale would retire every entry in the inbox the
    first time a harness with this feature ran."""
    hive = tmp_path / "hive"
    _entry(hive, "old.md")
    _entry(hive, "new.md", harness="deadbeefcafe")

    new, old = learnings.read_inbox(str(hive))  # read_dir sorts by ref

    assert not old.stale("0123456789ab")
    assert new.stale("0123456789ab")
    assert not new.stale("deadbeefcafe")
    assert not new.stale("")  # a caller with no harness in hand marks nothing


def test_reconcile_does_not_let_a_stale_entry_corroborate_a_live_one(tmp_path: Path) -> None:
    """An entry written when phases could not run `node` says nothing about a
    harness where they can, so it is not the second sighting that confirms."""
    hive = tmp_path / "hive"
    gone = _entry(hive, "task-1-db.md", task="task-1", harness="oldoldoldold")
    here = _entry(hive, "task-2-db.md", task="task-2", harness="newnewnewnew")

    assert learnings.reconcile(str(hive), "newnewnewnew") == []
    assert _meta(gone)["status"] == learnings.UNCONFIRMED
    assert _meta(here)["status"] == learnings.UNCONFIRMED
    # Same two files under the harness they were both written for: arithmetic.
    assert sorted(learnings.reconcile(str(hive))) == ["inbox/task-1-db.md", "inbox/task-2-db.md"]


def test_stamp_records_the_surface_this_tasks_entries_were_written_under(tmp_path: Path) -> None:
    """The phases cannot compute it, so the dispatcher writes it once the task
    is over — and never over a stamp that is already there, so a task resumed
    under a changed config does not backdate what it found earlier."""
    hive = tmp_path / "hive"
    mine = _entry(hive, "task-1-db.md", task="task-1")
    earlier = _entry(hive, "task-1-disk.md", task="task-1", harness="oldoldoldold")
    theirs = _entry(hive, "task-2-db.md", task="task-2")

    assert learnings.stamp(str(hive), "task-1", "newnewnewnew") == ["inbox/task-1-db.md"]
    assert _meta(mine)["harness"] == "newnewnewnew"
    assert _meta(earlier)["harness"] == "oldoldoldold"
    assert "harness" not in _meta(theirs)
    assert learnings.stamp(str(hive), "task-1", "") == []  # nothing to say, nothing written


def test_a_later_task_retires_an_entry_it_went_looking_for_and_did_not_find(tmp_path: Path) -> None:
    """The mirror of the confirmation rule: one task's word, but it only ever
    removes a row, so it runs unattended where promotion does not."""
    hive = tmp_path / "hive"
    wrong = _entry(hive, "task-1-node.md", task="task-1", rule="`node --test` is refused here.")
    _entry(hive, "task-2-node.md", task="task-2", refutes="inbox/task-1-node.md",
           rule="`node --test` runs; the refusal was a permission this harness no longer lacks.")

    learnings.reconcile(str(hive))

    assert _meta(wrong)["status"] == learnings.REFUTED
    assert _meta(wrong)["refuted_by"] == "task-2"
    assert learnings.reconcile(str(hive)) == []  # and it is not then confirmed by its own refuter


def test_a_task_cannot_refute_its_own_claim(tmp_path: Path) -> None:
    """That is a task changing its mind mid-run, which is what editing the
    entry would have been. No second sighting, no evidence."""
    hive = tmp_path / "hive"
    mine = _entry(hive, "task-1-node.md", task="task-1")
    _entry(hive, "task-1-again.md", task="task-1", refutes="task-1-node")

    learnings.reconcile(str(hive))

    assert _meta(mine)["status"] == learnings.UNCONFIRMED


def test_a_promoted_entry_is_not_retired_without_a_human(tmp_path: Path) -> None:
    """A human put it in the shared store on behalf of every project; one
    project's counter-example is a reason to look, not a verdict."""
    hive = tmp_path / "hive"
    shared = _entry(hive, "node.md", where=learnings.HARNESS_NAME, task="task-1",
                    scope=learnings.SCOPE_HARNESS)
    _entry(hive, "task-2-node.md", task="task-2", refutes="harness/node.md")

    learnings.reconcile(str(hive))

    assert _meta(shared)["status"] == learnings.UNCONFIRMED
    # The door for that one is the CLI's --refute, which is the same door as
    # every other ruling a human makes on the shared store.
    assert learnings.set_status(str(hive), "harness/node.md", learnings.REFUTED) is not None
    assert _meta(shared)["status"] == learnings.REFUTED


def test_the_phases_stop_being_handed_a_refuted_entry(tmp_path: Path) -> None:
    """Retiring one is about it no longer costing turns, not about hiding that
    it was ever written: the file stays, and so does the human's row."""
    hive = tmp_path / "hive"
    _entry(hive, "task-1-node.md", task="task-1", status=learnings.REFUTED,
           rule="`node --test` is refused here.")
    _entry(hive, "task-2-db.md", task="task-2")

    text = learnings.duties("implementador", "task-9", "myproj", str(hive))

    assert "`node --test` is refused here." not in text
    assert "Start postgres before the suite" in text
    assert "`node --test` is refused here." in learnings.table(learnings.read_all(str(hive)))


def test_the_table_cuts_the_retired_rows_first_and_flags_the_stale_ones() -> None:
    """What survives the cap is what a second task backed; what falls off it
    first is what a later task went looking for and did not find."""
    rows = [
        _row("inbox/a.md", status=learnings.REFUTED),
        _row("inbox/b.md", status=learnings.UNCONFIRMED, harness="oldoldoldold"),
        _row("inbox/c.md", status=learnings.UNCONFIRMED, harness="newnewnewnew"),
        _row("inbox/d.md", status=learnings.CONFIRMED),
    ]

    order = [line.split("`")[1] for line in learnings.table(rows, "newnewnewnew").splitlines()[2:]]

    assert order == ["inbox/d.md", "inbox/c.md", "inbox/b.md", "inbox/a.md"]
    assert "| unconfirmed (stale) |" in learnings.table(rows, "newnewnewnew")
    assert "(stale)" not in learnings.table(rows)
