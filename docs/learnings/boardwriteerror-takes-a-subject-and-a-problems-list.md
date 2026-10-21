# `BoardWriteError` takes a subject and a list of problems

**When it applies:** a test fakes `tasks.add_comment` (or another board write)
failing by raising `tasks.BoardWriteError`.

**Status:** unconfirmed. Reported by T-125-S1's implementer; the signature was
checked by its auditor.

## Why

`dispatcher/tasks.py` `BoardWriteError.__init__(self, subject, problems)` joins
`problems` into the message and keeps both as attributes. Raising it with one
string, as with a plain `ValueError`, fails with a `TypeError` before the code
under test sees a board error.

## Rule

Raise `tasks.BoardWriteError("<subject>", ["<problem>"])`: a subject and a list.

## Evidence

`tests/dispatcher/test_worker.py` approve tests where the comment write fails,
T-125-S1.
