# A backticked dotfile name under a directory reads as a path to the pointers gate

**When it applies:** you are writing a docs line under `docs/` that names a
dot-directory or dotfile under a path — front/.nitro, front/.output,
front/.dev.vars — and reaching for backticks.

**Status:** unconfirmed — reported once, by T-061's arquitecto. Its auditor
could not reproduce it (see *Why*), so read it, do not plan around it.

## Symptom

The arquitecto reported the pointers gate calling such names missing paths.
No gate output was kept.

## Why

`dispatcher/gates.py` `pointer_token` keeps a token with a slash whose last
segment matches `_HAS_EXTENSION` (a dot and 1 to 6 alphanumerics at the end),
and a dotfile segment such as `.nitro` or `.vars` matches: the gate treats it as
a path and looks for the file, which a checkout does not have because it is a
build output. Unverified half: `front/.gitignore` lists `.nitro`, `.output` and
`.dev.vars`, and `_ignored` / `_ignored_by_a_subproject` ask `git check-ignore`
before calling a token broken, so the ignore excuse may have covered these. The
auditor's own `git check-ignore` was refused in its phase.

## What to do

Name build outputs and dotfiles outside backticks in `docs/`, as
[a-docker-image-tag-reads-as-a-path-to-the-pointers-gate](a-docker-image-tag-reads-as-a-path-to-the-pointers-gate.md)
does for image tags. It costs nothing whether or not the excuse applies.

## Evidence

T-061 arquitecto handoff, *Proposed learnings*; `docs/decisions.md` ADR 97 and
`docs/implementations/T-061.md` keep these names out of backticks.
