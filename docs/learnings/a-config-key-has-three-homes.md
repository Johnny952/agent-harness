# A `config.yaml` key has three homes, and the suite guards none of them

**When it applies:** you added, renamed or removed a key in
`dispatcher/config.py` that an operator writes in `config.yaml`.

**Status:** confirmed — T-008 added `local_board` and found all three homes
stale in turn.

## The three homes

1. `config.example.yaml` — the commented template, and the place
   `docs/README.md` names as where a new key gets explained.
2. `scripts/configure.sh` — both its header paragraph and the `echo` lines that
   write comments into the generated `config.yaml`. The wizard writes no board
   block at all, so what it says about the options is all an operator sees
   there.
3. `README.md` (repo root) — the operator's manual: the component bullets, the
   operator section, and the "Known gaps (fix first)" narrative that says
   whether a feature is built or planned.

## Why it bites

Nothing loads `config.example.yaml` in the suite — the only test that mentions
it uses the filename as a string — so a block there is documentation and cannot
go red. `scripts/configure.sh` has no test either. A key can therefore be
correct in `config.py`, tested, and undocumented in all three places with a
green suite.

## Evidence

T-008: the arquitecto updated `config.example.yaml` only; the implementador had
to add `README.md`; the revisor's finding 1 added `scripts/configure.sh`. See
`docs/implementations/T-008.md` and `docs/decisions.md` ADR 2.
