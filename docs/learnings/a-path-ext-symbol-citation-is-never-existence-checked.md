# Nothing verifies the symbol half of a `file.py:symbol` citation, and a dotted symbol is checked as a path

**When it applies:** you are citing a function, class or constant as
`path.ext:symbol` in a doc under `docs/`, or you are relying on the pointers
gate to have checked a citation one of your own phases wrote.

**Status:** unconfirmed — the unchecked half was read out of
`dispatcher/gates.py` by T-024's revisor in round 2; the dotted-symbol
exception and the cost below were found by its auditor. No phase of T-024 ran
the gates; `docker` is refused in one.

## Symptom

No error, which is the problem. A citation naming a symbol that does not exist
passes every gate, every review round and the merge, and only fails when a
human follows the pointer.

T-024 shipped one through two review rounds: `docs/decisions.md` **ADR 53**
named `operator.format_status` as a `/usage` probe site. The probe is
`dispatcher/operator.py:_probe_into`, reached from
`account_reports(cfg, probe=True)` and so only from `cli.py`'s
`status --probe`; `format_status` is handed the finished reports and makes no
`claude` call at all. Two reviewing phases and four gate-aware writers read
that sentence.

## Why

`dispatcher/gates.py:pointer_token` takes the token's last slash-separated
segment and requires `_HAS_EXTENSION` — `\.[A-Za-z0-9]{1,6}$`, anchored at end
of string — to match it. For `dispatcher/dispatcher.py:_usage_record` that
segment is `dispatcher.py:_usage_record`, which ends in `_usage_record`: the
underscore is outside the character class, so nothing matches, `pointer_token`
returns `None`, and the token is not a pointer. Nothing downstream looks at it
again. The path half is not checked either, because the whole token was
dropped.

That half is already recorded, for the harness, in
`/data/.hive/learnings/harness/T-009-the-pointer-gate-accepts-both-citation-shapes.md`.
What it does not say is the exception.

**A symbol containing a dot can flip the behaviour**, because the regex only
needs *some* dot followed by 1–6 alphanumerics at the end of the segment, and a
dotted attribute supplies one. Measured, writing each as a backticked token and
reading `pointer_token`'s answer:

| Citation | Verdict |
|---|---|
| `docker_exec.py` + `ClaudeResult.raw`, joined by a colon | **a pointer** — `.raw` is 3 |
| `debt.py` + `Declaration.what`, joined by a colon | **a pointer** — `.what` is 4 |
| `debt.py` + `Declaration.origin`, joined by a colon | **a pointer** — `.origin` is 6 |
| `debt.py` + `Entry.resolved`, joined by a colon | ignored — `.resolved` is 8 |
| `dispatcher/dispatcher.py:_usage_record` | ignored — underscore |
| `front/src/lib/api/types.ts:Account.reserve_pct` | ignored — underscore |
| `observability/api/app.py:_read_cards` | ignored — underscore |
| `dispatcher/gates.py:pointer_token` | ignored — underscore |

The first three survive as pointers, so `_candidates` tries to resolve them as
*paths*, from the repo root and from beside the citing doc. No such file
exists, and the gate reports a broken pointer in a doc where nothing is broken.

So the shape is unchecked or wrongly checked depending on whether the symbol's
last dot-component is 1–6 alphanumerics — a distinction no writer is thinking
about, and the reason the first three rows above are spelled as two tokens
joined in prose: written as one backticked token they would break this very
file.

## Rule

Verify a symbol citation by hand — `grep -n "def <symbol>\|^<symbol>"` over the
file you named — because no gate will. Treat a passing `pointers` gate as
saying nothing about any `path.ext:symbol` in the diff.

When the symbol is dotted and its last component is short (`ClaudeResult.raw`,
`Declaration.what`), cite the file and the symbol as two tokens rather than
joining them with a colon, or the gate will call a correct citation broken.

The sibling trap, where a citation *is* checked and cannot ever resolve, is
[a-hive-path-cited-bare-is-a-broken-pointer](a-hive-path-cited-bare-is-a-broken-pointer.md).
For the two-symbol pair that caused T-024's near miss, see
[a-symbol-pair-that-reads-as-one-name](a-symbol-pair-that-reads-as-one-name.md).

## Evidence

Measured, not argued: the table above is `pointer_token`'s own answer for each
of those eight tokens, observed through a throwaway test in
`tests/dispatcher/` (the only way to run Python in a phase — `python3 -c` is
refused) and deleted after reading. The first draft of this entry had
`Entry.resolved` on the wrong side of the boundary, which is what the
measurement caught. `dispatcher/gates.py:pointer_token`, `_HAS_EXTENSION` and
`_candidates` were read by hand alongside. The ADR 53 error was confirmed
against `dispatcher/operator.py` (`account_reports`, `_probe_into`,
`format_status`) and `dispatcher/cli.py`'s `status` branch, and corrected in
place by T-024's auditor under
[an-adr-your-own-cycle-appended-is-not-yet-append-only](an-adr-your-own-cycle-appended-is-not-yet-append-only.md),
the entry alone being still unmerged.
