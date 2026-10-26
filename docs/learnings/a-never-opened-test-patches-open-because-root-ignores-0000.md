# A "never opened" test patches `open`, because root ignores mode `0000`

**When it applies:** you assert that code never opens or reads a file by
`chmod 0o000`-ing it and expecting a `PermissionError` on any read.

**Status:** unconfirmed. T-181's arquitecto foresaw it; the implementador
built the test that way. Same root cause as
[the-read-only-hive-write-test-skips-as-root](the-read-only-hive-write-test-skips-as-root.md).

## Symptom

No error: the suite runs as uid 0, so a `0000` file opens and reads fine, and a
test relying on `EACCES` passes on code that does read the secret.

## Why

Root bypasses file permission bits (`CAP_DAC_OVERRIDE`).

## Rule

Keep the `0000` chmod as the spec asks, but also monkeypatch `builtins.open`,
`io.open` and `os.open` with a guard that raises on the secret's realpath, as
`tests/dispatcher/test_credentials.py` `test_no_secret_file_is_ever_opened`
does.

## Evidence

`docs/decisions.md` ADR 462 *Consequences*; the test named above (T-181).
