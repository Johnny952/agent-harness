# Vendored role skills

Eight skills, vendored rather than installed, and delivered per role.

A skill is a set of instructions that only loads when it is relevant. These
eight are the ones the agent roles need: how to plan, how to test, how to
debug, how to review, how to take review. They live here, in the repo, for
three reasons:

- **Pruning.** Upstream skills are written for a human sitting in a terminal.
  They say "ask your human partner", they announce themselves in chat, they
  file GitHub review comments. None of that reaches anyone here: an agent runs
  headless under the dispatcher and its only channel to the next role is the
  task handoff. Every such instruction was rewritten to route through it. See
  `NOTICE.md` for what changed, per skill.
- **Versioning.** The agent containers mount `claude_shared` at
  `/root/.claude`, which tracks no version. A skill installed there drifts
  inside running containers independently of the image. A skill baked into the
  image moves when the image moves, and `git log` says why.
- **Per-role delivery.** A role should read the skills for its job and not the
  others'. Vendored, they can be handed out one directory at a time.

## Layout

One plugin per skill:

```
skills/
  <name>/
    .claude-plugin/plugin.json      the manifest the CLI reads
    skills/<name>/SKILL.md          the skill itself
    skills/<name>/*.md              reference files it links to
```

The doubled `<name>` is not a mistake. The outer directory is a *plugin*; the
inner one is the *skill* inside it. One skill per plugin, because
`--plugin-dir` is repeatable and a 1:1 mapping means the delivery set is just
a list of directories — no bundle groupings to invent or keep in sync.

## What goes to which role

| Skill | arquitecto | implementador | revisor | auditor |
|---|:-:|:-:|:-:|:-:|
| `writing-plans` | ● | | | |
| `test-driven-development` | | ● | | |
| `minimal-scope` | ● | ● | | |
| `systematic-debugging` | ● | ● | ● | ● |
| `receiving-code-review` | | ● | | |
| `verification-before-completion` | | ● | ● | |
| `blocking-review` | | | ● | |
| `code-quality-review` | | | | ● |

Two of these cross-reference each other, and the reference is hedged
("if it was delivered to you") exactly where the table says the pair does not
always travel together. `minimal-scope` defers outright to
`test-driven-development` when both are delivered: minimal scope shrinks the
solution, never the test.

`blocking-review` encodes the dispatcher's real verdict contract — the
`verdict` field of the revisor's structured return, with the last non-empty
line `VERDICT: APPROVED` / `VERDICT: CHANGES_REQUESTED` as the fallback for a
revisor that got no schema, failing closed on anything malformed. If
`revisor_approved()` in `dispatcher/dispatcher.py` ever changes, that section
changes with it.

## Validating a change

`claude plugin details` prints a plugin's component inventory and its
projected token cost. It is local and costs no quota, which makes it the check
to run after editing anything here. It takes a plugin *name*, so the directory
goes in `--plugin-dir`:

```sh
docker exec agent-cuenta1 \
  claude --plugin-dir <dir>/<name> plugin details <name>
```

Pointed at the parent directory instead, `--plugin-dir <dir>` loads every
child plugin and ignores the loose files, so `claude --plugin-dir <dir> plugin
list` checks all eight at once. Both are how a delivery set is validated
without spending a token of quota.

## Cost

Measured with `plugin details`, per skill: ~40–80 always-on tokens for the
frontmatter line the model always sees, and 1.3k–3.6k on-invoke for the body
it reads when the skill fires. Always-on totals per role, at the table above:

| Role | Always-on |
|---|---|
| arquitecto | ~150 tok |
| implementador | ~310 tok |
| revisor | ~190 tok |
| auditor | ~110 tok |

Always-on is charged on every call the role makes, so a skill added to every
role is not free. On-invoke is charged only when the model decides the skill
is relevant.

## Adding or changing a skill

- **Keep the prose upstream's** where it works. The fewer edits, the easier
  the next version bump. Rewrite an instruction only when it assumes something
  this harness does not have: an interactive human, a GitHub PR, a chat
  channel, a `/slash` command, a session that persists between phases.
- **No dangling pointers.** If a SKILL.md links a reference file, vendor that
  file too, or delete the link. A pointer to a file that is not there is the
  exact defect the auditor is meant to catch; it should not start here.
- **Record provenance in `NOTICE.md`** — upstream name, source URL, and what
  you changed. All three upstreams are MIT, which requires their copyright
  notices to travel with the copies; that file is where they live.
- **Nothing role-specific in a SKILL.md.** The role is chosen by which
  directories get delivered, not by a conditional inside the skill.
