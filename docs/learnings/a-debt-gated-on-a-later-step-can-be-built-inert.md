# A debt gated on a later step can be built ahead, inert

**When it applies:** a debt's `where` names a later plan step whose input
the fix needs (a header, a flag, a relay not built yet), and you are about
to defer the fix until that step lands.

**Status:** unconfirmed — proposed by T-149's arquitecto and implementador
(one source), checked against the tree by T-149's auditor.

## What happened

`T-146-D1` (no per-address sign-in throttle) waited on step 2 of
`docs/plans/server-deployment.md` *How it would be built*, which makes the
console send `X-Forwarded-For`. T-149 built the counter now
(`docs/decisions.md` ADR 381): `board_service/signin.py` `_forwarded_for`
returns None without the header, and `session_create` then reads, checks and
writes nothing per address, so the console's path is unchanged.
`test_another_address_and_no_header_are_unaffected` in
`tests/board_service/test_signin.py` pins the inert path.

## Rule

Build the fix behind the missing input, make it do nothing when the input is
absent, test that absence, and write the inert condition and what the later
step still owes (here: overwrite the client's header) into the ADR's
*Consequences*.
