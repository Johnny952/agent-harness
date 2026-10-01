# A `TaskFile` field's type is an annotation, not a guarantee — except `body`, which is always a `str`

**When it applies:** you are serving, guarding or testing a
`dispatcher/context_transfer.py:TaskFile` field, and about to trust either its
annotation or the guard around it.

**Status:** unconfirmed — both halves were read off `read_task_file` by T-011's
arquitecto and revisor. Neither has a failing case on disk: the `depends_on`
half has no card in this harness that trips it, and the `body` half is the
absence of a case rather than one.

## The two halves

`read_task_file` builds `depends_on` with `fm.get("depends_on", [])` and
validates nothing. The annotation says `list[str]`, so a card whose frontmatter
reads `depends_on: T-9` — a scalar, which YAML is happy to give — is parsed as
the string `"T-9"` and served as a string by `/api/tasks` and
`/api/tasks/<task_id>`. A console counting a dependency list gets 3 for that
card, and nothing warns. Contrast `kanban_issue_id`, which *is* type-checked and
has its own warning test: being on `TaskFile` says nothing about which of the
two a field is.

`body` is the opposite case. `read_task_file` builds it by splitting the file's
text on the frontmatter delimiter and `lstrip`ping newlines, so it is always a
`str` and always serialises. The `try` that `/api/tasks/<task_id>` assigns it
inside cannot fire on it, and no fixture can make it fire: a file with no body
yields `""`.

## What to do

Check which half your field is before you plan either a warning path or a test
for one. For a field `read_task_file` does not validate, the warning belongs
where the value is *used*, not where it is parsed — the reader's contract is
report-the-damage-never-crash and widening it inside `dispatcher/` is the wrong
end (`a-never-500-read-wraps-the-use-not-the-parse.md`).

For a field that cannot break the guard it sits in, keep the placement anyway
and say in the ADR that no test proves it, so a later reader does not go hunting
for one. `docs/decisions.md` ADR 21 does exactly that for `body`, in its closing
paragraph: the guard wraps the use of parsed values as a module rule, held to
even where this one field cannot break it, because the next field added there
may.

## Evidence

T-011: `dispatcher/context_transfer.py:read_task_file`,
`observability/api/app.py:_task` and the `/api/tasks/<task_id>` view,
`docs/decisions.md` ADR 21. The unvalidated `depends_on` was noticed while
confirming ADR 17's claim that the field is "already parsed as a `list[str]`" —
it is parsed, and it is not checked.
