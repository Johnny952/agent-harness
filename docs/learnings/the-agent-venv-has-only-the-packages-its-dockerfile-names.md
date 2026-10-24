# The agent venv has only the packages its Dockerfile names, not `pyproject.toml`'s

**When it applies:** you add a runtime dependency to `pyproject.toml` and are
about to write a test (or a module-level import in tested code) that imports
it.

**Status:** unconfirmed — reported by T-171's arquitecto (shared inbox
`T-171-the-agent-venv-has-only-the-packages-its-dockerfile-names`), applied by
T-171's implementador.

## Symptom

A probe test run with `python3 -m pytest <hive path>/test_probe.py -s -q`
printed:

```
waitress MISSING No module named 'waitress'
gunicorn MISSING No module named 'gunicorn'
flask 3.1.3 /opt/ia-harness/venv/lib/python3.11/site-packages/flask/__init__.py
```

## Why

The agent image's venv (`/opt/ia-harness/venv`) installs a hand-written list in
`docker/agent/Dockerfile` (pyyaml, flask, requests, pytest), not
`pyproject.toml`'s dependencies, and a phase cannot `pip install`. A package
added to `pyproject.toml` alone is absent from every phase's suite run. The
service images do `pip install .`, so the package does reach production.

## Rule

Import a new dependency inside the function that uses it, and test through a
stand-in put in `sys.modules` (or `importorskip`), never a module-level import.
`monkeypatch.setitem(sys.modules, "<name>", None)` makes the import raise, for
a test that checks what happens before it. T-171's `run()` in
`observability/collector/server.py` and `observability/api/app.py` is the
example (`docs/decisions.md` ADR 417); the same shape as
[the-push-route-tests-run-without-cryptography](the-push-route-tests-run-without-cryptography.md).

## Evidence

T-171 arquitecto, worktree `/data/projects/ia-harness/worktrees/T-171/work`, a
probe test under the task's hive directory.
