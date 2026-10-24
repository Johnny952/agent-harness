# An f prefix belongs to the line holding the placeholder

**When it applies:** you are re-wrapping a string in `dispatcher/project_docs.py`
built from implicitly concatenated literals (`_AUDITOR`, `_ARQUITECTO` and the
like) where some lines carry `{DEBT_DIR}`, `{DEBT_INDEX}` or another constant.

**Status:** unconfirmed — proposed by T-164-S3's implementador.

## What is true

Each literal in the parentheses is its own string, and only the ones written
`f"..."` are formatted. Re-wrapping text so that a `{placeholder}` moves onto
a line without the `f` leaves the braces in the prompt verbatim, and no test
in `tests/dispatcher/test_project_docs.py` checks for a literal brace. The
opposite trap is in `_IMPLEMENTADOR`, which is `str.format`ed whole: a literal
brace added there raises at dispatch time.

## What to do

After re-wrapping, check that every line holding a `{` starts with `f"`, and
that a line which lost its placeholder lost its `f` too.

## Evidence

T-164-S3's edit to `_AUDITOR` split the `DEBT_DIR` sentence across two lines
and had to keep the `f` on the second.
