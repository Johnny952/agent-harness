# A pre-receive hook never sees a dry-run push

**When it applies:** you want a local bare remote to refuse `_probe_push`'s
`git push --dry-run` (the write check in `dispatcher/docker_exec.py`,
`docs/decisions.md` ADR 214 and ADR 215) in a test or a measurement.

**Status:** unconfirmed — one phase measured it (T-117-S2's arquitecto, agent
container, git 2.39.5).

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
then exits 128. `test_check_reads_a_refusal_real_git_meets_at_receive_pack` in
`tests/dispatcher/test_project_setup.py` is the worked example.

## Evidence

`python3 -m pytest /data/.hive/tasks/T-117-S2/measure_test.py -s -p
no:cacheprovider` in the agent container, git 2.39.5 (ADR 215, *Consequences*).
