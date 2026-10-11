# ADR-55 — the directories under a task's scratch dir are 0755, whatever the umask

`docs/decisions.md` ADR 54 recorded, and did not fix, that the dispatcher makes
a task's scratch dir, `handoffs/` and `usage/` at its process umask, so under
077 they are 0700 and `observability/api/` — over a `:ro` mount, possibly as
another uid — cannot reach the 0644 files inside. This change is that fix, on
the writer, for all three directories at once, recorded as ADR 55. Done by the
operator out of cycle on 2026-10-10, with the dispatcher's worker accounts out
of quota. There is no debt row for it — the risk lived only in ADR 54 and in
`docs/learnings/a-0644-file-under-hive-still-sits-in-a-umask-directory.md` — so
this note is named by the ADR it adds rather than by a task or debt id. Scope:
`dispatcher/context_transfer.py`, one warning in `observability/api/app.py`,
their tests, ADR 55, this file. `front/`, `docker/` and `config.yaml` were not
opened, and nothing was run against a container.

## Verified first-hand

| Claim | Where | Verdict |
|---|---|---|
| `ensure_scratch_dir` makes the scratch dir at the umask | `os.makedirs(path, exist_ok=True)` | true; also the hive tasks dir if missing |
| `append_usage` makes `usage/` at the umask | `os.makedirs(os.path.dirname(path), exist_ok=True)` | true; also the scratch dir if missing |
| `save_handoff` makes `handoffs/` at the umask | through `_write_atomic`'s `Path.mkdir(parents=True, exist_ok=True)` | true; also the scratch dir if missing |
| The handoff file is 0644 whatever the umask | `_write_atomic` chmods the temp file before `os.replace` | true — no gap on the file half |
| The usage file is 0644 whatever the umask | `append_usage`'s `O_EXCL` + `fchmod` | true |
| `_UsageRecorder.record` swallows what `append_usage` raises | `dispatcher/dispatcher.py` | true, `except Exception` and a warning |
| The gate's `ensure_scratch_dir` is swallowed | `_run_gates`, `except Exception` | true |
| The cycle's `ensure_scratch_dir` and `save_handoff` propagate | `dispatcher/dispatcher.py`, cycle start and the end of the phase | true |

## The change

### `dispatcher/context_transfer.py`

`_makedirs_readable(hive_dir, path)`, beside `ensure_scratch_dir`. It refuses
with `ValueError` a `path` that is not under `hive_dir`; makes the hive tasks
dir with `os.makedirs(exist_ok=True)` at the umask, as before, and never
chmods it; then `os.mkdir`s each component below it in turn. A
`FileExistsError` skips the component untouched — it was there, or another
process won the race, and either way its mode is its creator's. A component it
did create is opened `O_RDONLY | O_DIRECTORY | O_NOFOLLOW` and `fchmod`ed to
`_DIR_MODE`, 0755, so a link a phase swaps in between the `mkdir` and the chmod
fails the open instead of redirecting it.

Three sites call it: `ensure_scratch_dir` for the scratch dir, `append_usage`
for `usage/` in place of its `os.makedirs`, and `save_handoff` for `handoffs/`
before `_write_atomic`. `_write_atomic`'s own mkdir is unchanged and now finds
the handoff's parent already there; it still makes a task file's parent — the
hive tasks dir — at the umask, which its docstring now says is deliberate.

The file modes needed no change: both writers were already umask-independent.

### `observability/api/app.py`

The `PermissionError` warning on `/api/tasks/<task_id>/usage` said the usage
directory "may be 0700 under the dispatcher's umask", which today's dispatcher
no longer does. It now names the scratch or usage directory, made at the umask
before ADR 55 or not by the dispatcher, and still cites ADR 54. The status, the
`data: null` and the path in the warning are unchanged.

## The `OSError` decision

The helper raises, chmod failures included, and each caller keeps the policy it
already had for a failed `mkdir` on the same line: `_UsageRecorder` logs and
the phase carries on, `_run_gates` logs and the phase goes to review ungated,
and the cycle's `ensure_scratch_dir` and `save_handoff` propagate. That is
`append_usage`'s stated rule — the module raises, the call site decides — and
it does not make anything newly fatal: an `fchmod` by the uid that has just
made the directory has no ordinary way to fail. Swallowing in the helper would
leave a 0700 directory silently, which is the failure this change exists to
remove.

## What was left standing, and why

- **Directories already on a host.** The helper never chmods what it did not
  create, so a scratch dir, `handoffs/` or `usage/` made 0700 by an earlier
  dispatcher stays 0700. Widening them is a one-off `chmod 755` by the
  operator; doing it in code would mean chmodding directories this call did not
  make, which the change rules out.
- **Other writers under the scratch dir.** The gates' `gates-round-<n>.log` and
  whatever a phase writes there are not these paths and keep their modes.
- **The learning.** `docs/learnings/a-0644-file-under-hive-still-sits-in-a-umask-directory.md`
  still says every directory is made at the umask and that no test covers the
  directory half; both are now false for these three directories, and its
  correction is left to whoever next edits that index.

## Tests

`tests/dispatcher/test_context_transfer.py`, all under a `tight_umask`
fixture that sets 077 and restores it: `save_handoff`, `append_usage` and
`ensure_scratch_dir` leave the scratch dir, `handoffs/` and `usage/` 0755 and
both files 0644, and a 0700 hive tasks dir stays 0700; a scratch dir that was
already there keeps its 0710 while the `usage/` and `handoffs/` made under it
are 0755; a missing hive tasks dir and its parent are made at the umask and not
widened; a path outside the hive raises `ValueError` and creates nothing; a
symlink planted where `usage/` goes is not chmodded through.

`tests/observability/test_api.py` — the `PermissionError` test also asserts the
warning says the directory predates ADR 55.

`.venv/bin/python -m pytest -q`: 1354 passed, 10 skipped.
