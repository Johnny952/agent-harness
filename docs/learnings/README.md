# Learnings

Things true of this project that cost a task time to find out. One file per
entry; this table is the index. Read the whole table, open only the entries
whose **When it applies** matches the task in front of you.

A trap that belongs to the harness rather than to this codebase — a refused
command, a worktree surprise — is not here: it waits in the shared inbox under
`/data/.hive/learnings/` until a human promotes it. `unconfirmed` means one
phase of one task reported it and nothing has reproduced it since: read it, do
not plan around it.

| Entry | Learning | When it applies | Status |
|---|---|---|---|
| [the-pytest-suite-is-the-whole-gate](the-pytest-suite-is-the-whole-gate.md) | `python3 -m pytest` is the only gate and the only interpreter: no linter, and `python3 -c` / `bash -n` are refused — observe values through a throwaway test | You are deciding what "green" means, or want to see a value, an exception or a shell script's syntax | confirmed |
| [the-kanban-seam-is-a-closed-surface](the-kanban-seam-is-a-closed-surface.md) | Every `KanbanClient` exposes exactly `enabled` plus the four methods; a public method on one client only fails a parity test unless that test's `LOCAL_BOARD_ONLY` names it, with an ADR and on the condition `dispatcher/dispatcher.py` never calls it; and `KanbanIssue`'s docstring is part of the diff | You are adding a method to a client in `dispatcher/vibe_kanban_client.py`, adding a third implementation, or changing `KanbanIssue` | confirmed |
| [a-config-key-has-three-homes](a-config-key-has-three-homes.md) | A `config.yaml` key must be documented in `config.example.yaml`, `scripts/configure.sh` and the root `README.md`; no test covers any of them | You added, renamed or removed a key an operator writes in `config.yaml` | confirmed |
| [atomic-writes-copy-state-machine](atomic-writes-copy-state-machine.md) | Copy `state_machine.py:_write_state` — temp file in the target directory, `os.replace`, no fsync — and keep temp names out of your own listing | You are adding code under `dispatcher/` that writes a JSON document another process reads | confirmed |
| [config-a-new-key-is-two-edits](config-a-new-key-is-two-edits.md) | A new `Config` field goes last with `= None` *and* into `_make_config`'s defaults in `tests/dispatcher/test_dispatcher.py`, or every dispatcher test fails | You added a field to `dispatcher/config.py`'s `Config` and the suite fails in a file you never touched | unconfirmed |
| [a-lookup-that-never-raises-catches-valueerror](a-lookup-that-never-raises-catches-valueerror.md) | Catch `(OSError, ValueError)`, never `json.JSONDecodeError`, and `FileNotFoundError` first when one case stays silent | You are writing a file-backed read in `dispatcher/` contracted to return `None` and never raise, keyed on an id from a task file | unconfirmed |
