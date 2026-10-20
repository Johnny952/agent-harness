# A pre-receive hook never sees a dry-run push

**When it applies:** you want a local bare remote to refuse the write check's
`git push --dry-run` (`dispatcher/docker_exec.py`, `_probe_push`; the
`docs/decisions.md` entry headed "`check` tells write access") in a test or a
measurement.

**Status:** unconfirmed — measured once by T-117-S2's arquitecto, carried into
the docs by T-117-S1.

## Symptom

A `pre-receive` hook that exits 1 on the bare does not refuse the probe:

```
exit: 0
*	HEAD:refs/heads/ia-harness/write-check-0123456789ab	[new branch]
Done
```

## Why

A dry-run push connects to receive-pack and reads its ref advertisement, then
stops: it sends no ref update commands, so no hook runs.

## Rule

Make the refusal at the transport: a stand-in `ssh` (`GIT_SSH` /
`GIT_SSH_COMMAND`) that writes the error to stderr and exits non-zero — git
then exits 128.

## Evidence

A throwaway pytest under T-117-S2's hive task directory, run with `-s` in the
agent container on git 2.39.5.
