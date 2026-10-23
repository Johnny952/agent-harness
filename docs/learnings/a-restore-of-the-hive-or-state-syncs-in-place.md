# A restore of the hive or `/state` syncs in place: both roots are mount points

**When it applies:** you are writing code that replaces the whole hive or the
whole `<state_dir>` (a restore, a reset, a migration that rebuilds a root),
and are reaching for "rename the live directory aside, move the new one in".

**Status:** unconfirmed — proposed by every phase of T-147 and built into
`docs/decisions.md` ADR 328, never seen failing in a real container.

## Symptom

None seen: the trap was found by reading, before anything was built. A root
that is a mount point cannot be renamed from inside the container, and even
a rename done on the host leaves the board service on the old tree, because
`docker/compose/docker-compose.yml` mounts `dispatcher_state/actions` into it
as a second, separate bind mount, which stays on the old inode.

## Why

The hive and `dispatcher_state` are bind mounts in both compose files, and
`actions/` is mounted on its own into the board service. A directory that is
a mount point cannot be swapped by rename, and a second mount of a
subdirectory follows the inode, not the path.

## What to do

Sync file by file inside the existing roots: write each file over the live
one (temp file in the same directory, `os.replace`), create missing
directories, keep existing ones, and move what the source lacks into an aside
directory under the same root. `dispatcher/backup.py` `_sync` is the model.
Since T-156 (ADR 351) it also moves aside every live file it overwrites with
different bytes, and never follows a live symlink.

## Evidence

T-147's arquitecto, reading both compose files (ADR 328 *Context* and
*Ruled out*); `tests/dispatcher/test_backup.py`
`test_restore_round_trip_sets_aside_and_keeps_directories` pins that the
directories keep their inode.
