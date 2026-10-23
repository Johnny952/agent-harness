# A run-line mount on `/backups` collides with the `dispatcher` service's own

**When it applies:** you add a `-v ...:/backups` (or any `-v` onto a path the
service already mounts) to a `docker compose run ... dispatcher` line in a
host script, or you change the `dispatcher` service's volumes in
`docker/compose/*.yml`.

**Status:** unconfirmed. T-158's arquitecto and implementador both reported
it, which is one source; the auditor checked it against the compose files and
the scripts, not against a real docker engine.

## Why

Since T-158 the `dispatcher` service mounts `${BACKUP_DIR:-../../.data/backups}`
at `/backups` (`docs/decisions.md` ADR 360). A run-line `-v` onto the same
container path is a second mount on one target: whether compose replaces the
service's or the engine refuses it is untested here.

## Rule

Mount a script's own copy of the backup directory at another path —
`scripts/restore.sh` uses `-v "$BACKUP_DIR:/restore:ro" -e BACKUP_DIR=/restore`
— and let `scripts/backup.sh` rely on the service mount with no `-v`/`-e`.

## Evidence

`git diff 295cda8 HEAD -- docker scripts` on T-158's branch: both compose
files' `dispatcher` volumes end in the `/backups` mount, and `restore.sh`'s
run line mounts `/restore:ro`.
