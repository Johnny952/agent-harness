# A Markdown file in `front/` is format-checked only by prettier, which a phase cannot run

**When it applies:** you wrote or are fixing a `.md` file under `front/` (a
README's table, say) and want it in prettier's shape, or you are counting on
`bun run lint` or `bun run lint --fix` to catch or repair its format.

**Status:** unconfirmed — reported by T-117-S1's implementador; its auditor
read `front/eslint.config.js` and ran `bun run lint` and `bun run test` green.

## Symptom

`bunx prettier --write` and `bunx prettier --check` are refused in a phase
("requires approval"), and the lint is green on a file prettier flags.

## Why

`front/eslint.config.js` lints `**/*.{ts,tsx}` only, so the `prettier/prettier`
rule never sees a Markdown file and `bun run lint --fix`
([bun-run-lint-fix-applies-the-prettier-format](bun-run-lint-fix-applies-the-prettier-format.md))
cannot rewrite it. `front/.prettierrc` sets no `proseWrap`, so prettier leaves
prose alone and reformats tables: every cell padded to its column's widest, the
delimiter row widened to match.

## Rule

Hand-align the table (cell width counts characters, an em dash is 1, though
`cut -c` counts it as 3 bytes) and file `bunx prettier --check src` in `front`
as a command request; the operator's run is the arbiter.

## Evidence

T-117-S1: the hand-aligned Conventions table in `front/src/routes/README.md`
passed the operator's `bunx prettier --check src` (exit 0, "All matched files
use Prettier code style!").
