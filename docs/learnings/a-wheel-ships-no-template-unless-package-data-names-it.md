# `pip install .` drops every non-Python file `package-data` does not name, and running from a checkout hides it

**When it applies:** you added a file a module loads from beside itself at
runtime — a Jinja template, a `.sql` schema, a static asset — anywhere under
`dispatcher/`, `observability/` or `hooks/`.

**Status:** unconfirmed — reported by T-010's arquitecto as a risk and by its
implementador as a learning; the wheel itself has never been built here, because
`pip wheel` is refused inside a phase.

## Symptom

No error, and no way to get one from a phase. `observability/board/` renders
every screen under `python3 -m pytest` and under `python -m
observability.board.app` from a checkout, because in both cases the module is
being imported from the source tree and `templates/` is sitting next to it. The
image is the only place it breaks: `observability/board/Dockerfile` runs `RUN
pip install --no-cache-dir .`, and a wheel built without the `package-data`
entry contains `app.py` and no `templates/`, so every route answers
`jinja2.TemplateNotFound`.

## Why

`[tool.setuptools.packages.find]` finds *packages*, which means `.py` files.
Anything else is data and has to be named in `[tool.setuptools.package-data]`.
The two ways this project runs the code disagree about whether that matters:
`python -m` from `/app` resolves the checkout first and never consults the
installed distribution, so the omission is invisible everywhere except inside a
freshly built image.

## What to do

Add the entry to `pyproject.toml` `[tool.setuptools.package-data]` in the same
change that adds the file — `"observability.collector" = ["schema.sql"]` is the
older half of the pair and `"observability.board" = ["templates/*.html"]` is
T-010's — and then assert it in the suite rather than reading it back. A phase
cannot build the wheel to check (`docker build`, `pip wheel` and `docker info`
are all refused), so the assertion is the only evidence that will exist before a
human runs the image:
`tests/observability/test_board.py:test_the_wheel_the_image_installs_ships_the_templates`
is the shape to copy.

## Evidence

T-010, `docs/implementations/T-010.md` "Not done": the `package-data` entry and
its test were added precisely because the image build could not be run to catch
the omission. `pyproject.toml` `[tool.setuptools.package-data]` holds both
entries.
