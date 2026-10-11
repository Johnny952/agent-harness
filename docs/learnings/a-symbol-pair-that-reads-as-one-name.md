# `dispatcher.py` has four near-identical recorder names, and citing the wrong one reads as a broken symbol

**When it applies:** you are citing one of `dispatcher/dispatcher.py`'s
recorder helpers in a doc, a docstring or a review finding — or you are naming
a new "build a record" / "write a record" pair anywhere under `dispatcher/`.

**Status:** unconfirmed — reported by T-024's revisor in round 2, which checked
that T-024's own docs cited the right half each time.

## Symptom

No error. A citation that looks correct, names a symbol that exists, and points
at the wrong function — so a reader following it finds code that does not do
what the sentence claims.

## Why

`dispatcher/dispatcher.py` now holds four names for two jobs, in two pairs that
each read as one name:

| Symbol | What it is |
|---|---|
| `_probe_record` | **Builds** a `/usage` probe's record (pure, ADR 49) |
| `_record_probe` | **Writes** one, swallowing and logging any failure |
| `_usage_record` | **Builds** a `claude` call's usage line (pure, ADR 53) |
| `_UsageRecorder` | **Writes** one, swallowing on `_record_probe`'s contract |

`_probe_record` and `_record_probe` are the same two words in the other order.
Both exist, so a wrong citation does not fail a grep, does not fail the
pointers gate — which never checks the symbol half of a `path.ext:symbol`
citation at all
([a-path-ext-symbol-citation-is-never-existence-checked](a-path-ext-symbol-citation-is-never-existence-checked.md))
— and does not fail review unless the reviewer happens to open the file.

The split itself is deliberate and worth keeping: a pure builder is testable
without a filesystem, and a writer that swallows must not also be the thing
that decides what to write. ADR 53's recorder was built on that shape on
purpose. It is only the naming that collides.

## Rule

When you cite one, say which job it does in the same clause — "`_probe_record`
builds", "`_record_probe` writes" — so the sentence falsifies itself if the
name is wrong. When you add a pair, do not name it by reordering two words:
`_usage_record` / `_UsageRecorder` is the better precedent, since the dataclass
name cannot be mistaken for the function.

## Evidence

`grep -n "_probe_record\|_record_probe" dispatcher/dispatcher.py` in the T-024
worktree returns both definitions and both call sites, plus two docstring
citations added by T-024 (`_usage_record`'s, citing `_probe_record` for the
`now`-parameter shape, and `_UsageRecorder.record`'s, citing `_record_probe`
for the swallow contract) — each pointing at the right half, which is what the
round-2 revisor verified.
