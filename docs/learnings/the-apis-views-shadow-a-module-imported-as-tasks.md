# The api's views are named `tasks` and `task`, so `dispatcher.tasks` is imported there as `task_store`

**When it applies:** you are calling `dispatcher/tasks.py` from
`observability/api/app.py`, or tidying that file's imports.

**Status:** unconfirmed — reported by T-026's implementador and checked by the
auditor against the file; nobody ran the shadowed form to see it fail.

## What is true

`create_app` in `observability/api/app.py` defines its two task views as
nested functions named `tasks` and `task`. Inside `create_app` those names
are the views, so a module imported as `tasks` at the top of the file is
shadowed exactly where it is needed: `tasks.read_description(...)` inside a
view would look the attribute up on the view function. The failure is an
`AttributeError` at request time, which the route's never-500 guard does not
catch (`a-never-500-read-wraps-the-use-not-the-parse.md` lists the tuple), so
it would be a 500 and not a warning.

The file therefore has `from dispatcher import tasks as task_store`. It reads
like a stylistic alias and is not one.

## What to do

Keep the alias, and use `task_store` for every call into the module from that
file. Do not rename the views to free the name: Flask derives the endpoint
names from them.

## Evidence

T-026: `observability/api/app.py`, the import of `task_store` and the `tasks`
and `task` views inside `create_app`.
