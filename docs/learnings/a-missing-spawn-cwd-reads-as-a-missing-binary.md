# A missing `spawn` cwd reads as a missing binary

**When it applies:** a walk script that spawns `bun run dev` fails with
`spawn bun ENOENT`, and `bun` runs fine from your shell.

**Status:** unconfirmed. Reported by the T-178-S3 implementador.

## Symptom

```
spawn bun ENOENT
```

## Why

Node's `child_process.spawn` reports a `cwd` that does not exist with the same
`ENOENT` error, naming the command, as a command that is not on `PATH`. The
T-178-S3 walk resolves its front dir as `<cwd>/front` unless given one, so run
from `front/` it spawns in `front/front`, which is not there. A granted
`cd front && bun run …` earlier in the phase leaves the shell in `front/`, so
the next `node` call lands there by default.

## What to do

Check the `cwd` you hand `spawn` before the binary: run the walk from the
worktree root, or pass the front dir as its argument.

## Evidence

T-178-S3 implementador, `node <hive_tasks_dir>/T-178-S3/verify/walk.mjs`
run from `front/`.
