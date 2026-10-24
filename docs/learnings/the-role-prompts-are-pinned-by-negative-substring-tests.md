# The role prompts are pinned by negative substring tests

**When it applies:** you are editing the duty text of a role in
`dispatcher/project_docs.py` (`_ARQUITECTO`, `_IMPLEMENTADOR`, `_REVISOR`,
`_AUDITOR`) and are choosing the words for it.

**Status:** unconfirmed — proposed by T-164-S3's arquitecto and implementador.

## What is true

`tests/dispatcher/test_project_docs.py` pins the prompts with substring
tests, and some of them assert a word is absent:

- `test_no_other_role_is_told_about_a_split` fails if the word `split`
  appears anywhere in a role's duties other than the arquitecto's.
- The arquitecto's text after `Return a split` must not contain `card`.
- The first paragraph of the auditor's text from `Debt works the other way
  round.` must not contain `card`.

So the next slice of a goal is worded "the next slice" or "the next task"
outside the arquitecto, never "split".

## What to do

Grep the test file for `not in` before choosing words for a prompt edit, and
run `python3 -m pytest tests/dispatcher/test_project_docs.py`.

## Evidence

T-164-S3 wrote R1 into `_IMPLEMENTADOR` and `_REVISOR` as "next slice" for
this reason; the suite passes with it.
