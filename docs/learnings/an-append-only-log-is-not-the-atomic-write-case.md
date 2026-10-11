# An append-only log under `.hive/` is not the atomic-write case, and its integrity is the reader's half

**When it applies:** you are adding something under `dispatcher/` that writes a
file another process reads, and
[atomic-writes-copy-state-machine](atomic-writes-copy-state-machine.md)'s
trigger has fired — but what you are writing is a growing **log**, one final
record per line, rather than a document that gets replaced.

**Status:** unconfirmed — the exception was reasoned by T-024's arquitecto,
built by its implementador and checked by both its review rounds, which is one
task and so one source.

## Symptom

No error. A log re-serialised in full on every append, getting slower as the
series it exists to accumulate grows — or, the other way, an aggregator that
treats a torn last line as a damaged file and refuses the whole series.

## Why

`_write_atomic` and `state_machine.py:_write_state` exist for a JSON
**document** another process may read while it is being replaced: temp file in
the target directory, `os.replace`, so a reader never sees half of it. Every
word of that applies to a card, a state file or an index, and none of it to a
log. A line in a log is final the moment it is written; there is nothing to
replace, no reader that could see a half-document, and re-serialising the file
per record would buy nothing and cost more each time.

`dispatcher/context_transfer.py:append_usage` is this module's one deliberate
departure, and `docs/decisions.md` **ADR 53** is where the departure is argued.
It appends with `O_APPEND` and does not go through `_write_atomic`.

**The single writer is a convention, not a guarantee.** It is tempting to
reason that the dispatcher holds the task lock and is therefore the only thing
writing — but `docker/compose/docker-compose.agents.yml` mounts `../../.hive`
at `/data/.hive` **read-write** into every agent container, at the same path
the dispatcher uses. A phase can append to any of these files. The lock orders
the dispatcher against itself and nothing else.

## Rule

Write the log with `O_APPEND`, and put the integrity in the **reader's**
contract rather than the writer's. State it where the writer lives, so the
first aggregator reads it:

- skip a line that will not parse, rather than calling the file damaged;
- do not assume the last line is complete;
- never assume a single writer.

Then say in the docstring why `_write_atomic` was skipped, because the next
reader of that module will otherwise count it as an oversight — this entry
exists because that was the first question both of T-024's review rounds asked.

The mode is **not** part of the departure: a log under `.hive/` gets the same
explicit 0644 every file there does, and getting that right has its own trap in
[a-0644-file-under-hive-still-sits-in-a-umask-directory](a-0644-file-under-hive-still-sits-in-a-umask-directory.md).

## Evidence

`dispatcher/context_transfer.py:append_usage` — its docstring carries the whole
argument and the reader's half of the contract; `_write_atomic` above it is the
case it is not. `docs/decisions.md` ADR 53, last paragraphs of *Consequences*.
The read-write agent mount is `docker/compose/docker-compose.agents.yml`, two
services, plus `docker-compose.coolify.yml`; the api's is the same path
`:ro`, in `docker/compose/docker-compose.yml`.
