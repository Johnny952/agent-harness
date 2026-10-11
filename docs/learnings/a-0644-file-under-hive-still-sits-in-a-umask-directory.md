# A cross-uid read under `.hive/` needs the directory's mode too, and only the file's is ever set

**When it applies:** you are making a file the dispatcher writes under `.hive/`
readable by another uid — an agent container, `observability/api/` over its
`:ro` mount — and have set, or are about to set, the file's mode to 0644.

**Status:** unconfirmed — reported by T-024's revisor in round 2 as a
worth-knowing finding, after the file half had been fixed and tested in that
same round. Not observed live: nothing reads that log yet.

## Symptom

No error text of its own. A file whose mode is provably 0644 that the named
reader still cannot open:

```
Permission denied: <hive_tasks_dir>/<task_id>/usage/calls.jsonl
```

## Why

`dispatcher/context_transfer.py` sets a **file's** mode on purpose and creates
every **directory** at the umask. `_write_atomic` chmods 0644 before its
`os.replace`, and `append_usage` creates its log `O_EXCL` and `fchmod`s the
descriptor — but `scratch_dir`'s `os.makedirs` and `append_usage`'s own
`os.makedirs` both take whatever the dispatcher process's umask is. Under
`027` or `077`, the 0644 file sits in a 0700 directory and no other uid can
traverse to it.

The file half is the half that has a rule and a test; the directory half has
neither, in this module or anywhere under `dispatcher/`.

Since `docs/decisions.md` **ADR 55** (2026-10-10, out of cycle) that is no
longer true of the scratch dir, `handoffs/` and `usage/`:
`context_transfer._makedirs_readable` makes each directory it creates below the
hive tasks dir 0755 whatever the umask, and `ensure_scratch_dir`,
`save_handoff` and `append_usage` go through it. It leaves alone what it did
not create — a directory made before ADR 55, the hive tasks dir itself, and
whatever a phase or the gates log makes — so the trap still applies to any
other directory on the path, and to a new writer that calls `os.makedirs`.

## The file half, and how to get it right

Worth knowing alongside, because the two are usually written in one go.

An explicit 0644 **is** this module's rule for anything under `.hive/`, not an
optimisation: `_write_atomic`'s own comment gives the reason — agent
containers, possibly non-root, and the host operator both read what the
dispatcher puts there — and
[atomic-writes-copy-state-machine](atomic-writes-copy-state-machine.md) records
`dispatcher/learnings.py:_write` adding the same chmod for the same reason. The
umask is not that rule. `open(path, "a")` and `open(path, "w")` both create at
the umask, so neither is enough on its own.

When you create the file yourself rather than replacing it, create it
`O_EXCL` with the mode and `fchmod` the descriptor:

- **on the descriptor**, so there is no window between create and chmod and no
  path to race;
- **only at creation** — catch `FileExistsError` and do nothing — so the code
  never changes the mode of a file some other uid owns;
- **not after the write**: a chmod that fails there makes the caller log a
  failure for a write that actually landed.

A plain `os.chmod(path, ...)` after the fact was rejected in T-024 for the
first and third of those reasons.

## Rule

Check the mode of every directory on the path, not just the file, before
claiming a reader in another container can open it.

## Evidence

Read by hand in `dispatcher/context_transfer.py` — `scratch_dir`,
`ensure_scratch_dir`, `_write_atomic`, `append_usage` — by T-024's revisor in
round 2 and confirmed by its auditor against the same four symbols. The file
half is covered by
`tests/dispatcher/test_context_transfer.py:test_append_usage_leaves_the_log_readable_whatever_the_umask`,
which was red at 0600 under a `077` umask before the `fchmod`. The directory
half is covered since ADR 55 by
`test_the_directories_a_task_gets_are_traversable_whatever_the_umask` in the
same file.
