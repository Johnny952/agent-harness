# A task card's frontmatter can quote the heading you are matching, so match the parsed body

**When it applies:** you are asking a task card under `.hive/tasks/` a
structural question — did this role's phase run, how many rounds were there,
which phases returned — and you are about to answer it by searching the file.

**Status:** unconfirmed — reported by T-014's arquitecto, which found the
collision in T-014's own card before writing the matcher, and pinned by the
implementador in `tests/dispatcher/test_context_transfer.py`.

## Symptom

No error. A predicate over the card answers `True` for a task whose phase never
ran, because the string it matched is in the task *description* rather than in
the record of the phases. T-014's own card is an instance: its frontmatter
quotes the heading the task is asking for, more than once, in sentences like

```
no `## auditor` section, no drop, and `merge-task` prints what it kept and why
```

and the file's text therefore contains `## auditor` before any phase has run at
all.

## Why

`context_transfer.task_file_path(hive_dir, task_id)` is one file with two
halves, and only the second is the dispatcher's record.

The frontmatter carries `description`, which is operator or main-thread prose of
any length and may quote anything — headings, code fences, other cards' text.
The body is what `context_transfer.handoff` appends to, one `## <label>` block
per phase that returned, rendered by `dispatcher/handoff.py:body` over the label
the phase loop builds. `read_task_file` splits the two on the `---` delimiters
and hands back `TaskFile.body` already separated.

A second collision sits inside the body: a role writes prose into its
`**Detail**` and its `**Risks**`, and that prose lands *inside* a section. So a
substring search for the role's name is not the test either — "the auditor never
ran" is a sentence a revisor writes, in a card that has no auditor section.

## What to do

Read `read_task_file(task_file_path(hive_dir, task_id)).body` and search that,
never the file's text. Match the heading anchored, with exactly two hashes:
`^## <role>$`, `re.escape` on the role, `re.MULTILINE`. Accept the
`## <role> (round N)` form as well — `run-phase --round` labels a hand-resumed
phase that way, and a gate that misses it answers "never ran" for every
recovered cycle. `dispatcher/context_transfer.py:has_phase_section` is the
worked example, and the reason the heading is evidence at all is in
[`docs/decisions.md`](../decisions.md) ADR 34.

The structural limit of trusting a heading — `fallback_body` renders an
unparseable phase's prose unindented, so prose can forge one — is
[`T-014-D1`](../debt/T-014-D1.md), not something a careful matcher fixes.

## Evidence

`tests/dispatcher/test_context_transfer.py`, the four `has_phase_section` cases:
the heading and not the role's name is the test, `(round 2)` counts, every
unreadable card answers `False`, and the body-not-file-text case asserts `False`
for a card whose frontmatter contains `## auditor` while its body does not.
That last one is non-vacuous — the string is in the file and the answer is
still `False`.
