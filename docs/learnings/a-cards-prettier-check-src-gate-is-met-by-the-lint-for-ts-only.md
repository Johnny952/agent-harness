# A card's `prettier --check src` gate is met by `bun run lint` for `.ts`/`.tsx` only

**When it applies:** your card's *Done when* lists `prettier --check src` for
`front/` beside typecheck, lint and test, and you are about to run it.

**Status:** confirmed by T-120, whose implementador and revisor found the
same refusal — first reported by T-118-S2's arquitecto and implementador;
its auditor hit the same refusal and read the config. T-118-S3's
implementador hit it again and got the check run as a command request.

## Symptom

`front/package.json` has no check script (only `format`, which is
`prettier --write .`), and the bare call is refused in a phase:

```
This command requires approval
```

for `bunx prettier --check src` (and, per the implementador,
`node_modules/.bin/prettier`).

## Why

`front/eslint.config.js` imports `eslint-plugin-prettier/recommended`, so
`bun run lint` reports every prettier difference in `*.{ts,tsx}` as a
`prettier/prettier` error. It never reads the two other files under `src/`,
`styles.css` and `routes/README.md` — see
[a-markdown-file-in-front-is-format-checked-only-by-prettier](a-markdown-file-in-front-is-format-checked-only-by-prettier.md).

## Rule

Make one attempt at `bunx prettier --check src`; when it is refused, count a
clean `bun run lint` as the check for `.ts`/`.tsx`, say so, and say whether the
diff touched `styles.css` or a `.md` under `src/`. To meet the gate itself,
file `bunx prettier --check src` in `front` as a command request: the
operator runs it and the log lands under the task's `commands/` directory
(T-118-S3).

## Evidence

T-118-S2 auditor, 2026-10-21, from `front/`: `bunx prettier --check src`
answered `This command requires approval`; `bun run lint` printed
`$ eslint .` and exited clean; the diff touched no non-TS file under `src/`.

T-118-S3 implementador, 2026-10-21: the same refusal; the command request
was run by the operator, and `<hive_tasks_dir>/T-118-S3/commands/1.log`
reads `All matched files use Prettier code style!`, exit 0.
