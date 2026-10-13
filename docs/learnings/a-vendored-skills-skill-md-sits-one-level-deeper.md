# A vendored skill's SKILL.md sits one level deeper than its name

**When it applies:** you are told to use one of the role skills under
`skills/` (blocking-review, code-quality-review, systematic-debugging…) and
open `skills/<name>/SKILL.md` by path rather than through the Skill tool.

**Status:** unconfirmed — reported once, by T-037's revisor (carried from the
shared inbox); the auditor checked the layout with `find skills -name
SKILL.md`.

## Symptom

`skills/blocking-review/SKILL.md` does not exist. The directory holds only a
`skills/` subdirectory, and the file is at
`skills/blocking-review/skills/blocking-review/SKILL.md`.

## Why

Each vendored skill is packed as a plugin directory: `plugin_dirs` in
`dispatcher/role_skills.py` hands the CLI one `--plugin-dir` per skill, and
a plugin keeps its skills under its own `skills/`. So every one of the eight
skills under `skills/` has its SKILL.md at `skills/<name>/skills/<name>/`.

## Rule

Look under `skills/<name>/skills/<name>/SKILL.md`, or run
`find skills/<name> -name SKILL.md`.

## Evidence

`ls skills/blocking-review/` prints only `skills`, and
`find skills -name SKILL.md` lists all eight at that depth (main at
`7cc44ca`, and again in the T-037 worktree).
