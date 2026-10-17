# Plan: editing the dispatcher's settings from the console

**Status:** designed 2026-10-15 by T-055, and nothing is built. The decisions
are `docs/decisions.md` ADR 75. This file only orders the build. It
answers `docs/plans/backlog.md` item 0 and the *Role models write* bullet of
`docs/plans/front.md` tier 3.

The shape, in one paragraph. `config.yaml` stays as written and holds the
defaults. The console's saves land in `/app/config.d/overrides.yaml`, which
is `<repo>/config.d/overrides.yaml` on the host. `load_config` merges that
file over `config.yaml`, replacing each top-level key whole, and validates
the result. A small Python service, `config-writer`, is the only thing that
writes the file. It has its own bearer token, its own network and one
read-write mount, the `config.d` directory. The console reaches it through
its server half. The dispatcher reads the settings at the start of each
verb, so a running task keeps the settings it started with.

There are four tasks below. Each changes one surface, and they run in this
order. Each task's arquitecto reads ADR 75 first. A task that finds ADR 75
wrong appends a narrowing ADR and does not edit ADR 75.

## 1. The `load_config` merge

**Surface:** the contract of `dispatcher/config.py` `load_config`, and its
tests in `tests/dispatcher/test_config.py`.

**Builds.**

- **The key table.** A module-level table holding every key `load_config`
  reads, with its class (`editable`, `read-only`, `credential`) and the
  one-line reason from ADR 75 part 5. It is a public name, so that the
  writer can import it.
- **The `overrides_path` keyword.** `load_config` gains it, defaulting to
  `config.d/overrides.yaml` beside the config path.
- **Reading the file.**
  - A missing file, or one that is empty or holds only comments, means no
    overrides.
  - A file that does not parse, or is not a mapping, raises `ValueError`.
  - A key that is not *editable* raises `ValueError`, and so does a `null`
    value.
  - Each value gets the strict type check for its key's kind.
- **The merge.** The overrides replace top-level keys whole, and every
  existing check then runs on the merged mapping.
- **Error messages.** A failure caused by the overrides file names its path
  and says to reset the key from the console or delete it from the file.
- **`.gitignore`** gains `/config.d/`.
- **The docs.** `config.example.yaml` and the root `README.md` document the
  directory (`docs/learnings/a-config-key-has-three-homes.md`).

**Done when.**

- With no overrides file, every existing test of `tests/dispatcher/test_config.py`
  passes unchanged.
- Tests cover each of these:
  - an absent, an empty and a comment-only file;
  - a scalar override;
  - a `role_model` override replacing the base map whole, and `{}`
    emptying it;
  - a `fallback_roles` list replaced whole;
  - a refusal for a non-editable key, for `null`, for a string `"true"` on
    a boolean key, and for a file that is not a mapping;
  - a merged-result refusal (`reserve_pct` overridden above the base
    `quota_threshold_pct`) whose message names the overrides path;
  - an explicit `overrides_path` reaching a file outside `config.d`.
- One test asserts that the table's keys are exactly the keys `load_config`
  reads. It loads a config that sets every key in the table, and checks the
  table against `Config`'s fields plus the removed board keys.
- `python3 -m pytest` is green.

**Built 2026-10-17** by T-064: `CONFIG_KEYS` (39 keys: 22 editable, 15
read-only, 2 credential) and `load_config(path, *, overrides_path=None)` in
`dispatcher/config.py`. `docs/decisions.md` ADR 106 narrows ADR 75: it adds
`auto_approve` and `protected_paths` as read-only, refuses unknown keys, and
says a merged-result error is the overrides' only when `config.yaml` alone
loads. Nothing writes `config.d` and no service mounts it yet.

## 2. The writer service

**Surface:** the `config-writer` service. That covers its package
`config_writer` at the repo root, its `Dockerfile`, its routes and its tests
under `tests/config_writer`, and its entries in both compose files. Reads
the key table and `overrides_path` from task 1.

**Builds.**

- **The routes,** `GET /settings`, `PUT /settings/<key>` and
  `DELETE /settings/<key>`. Their bodies, envelope and status codes are in
  ADR 75 part 4.
- **The write,** from ADR 75 part 3:
  - the `flock`;
  - the revision check;
  - the temp file validated by `load_config`;
  - `overrides.yaml.prev`;
  - the `os.replace`;
  - the `changes.jsonl` line;
  - the rollback when the log append fails.
- **The token check.** `CONFIG_WRITER_TOKEN` is checked with
  `observability/auth.py`. The service refuses to start without it and
  exits 2 naming it.
- **Compose.**
  - The `config-writer` service goes in `docker-compose.yml` and
    `docker-compose.coolify.yml`.
  - Its mounts are `config.yaml` `:ro` and `config.d` read-write.
  - It publishes `127.0.0.1:8790` and joins `config_writer_net` only.
  - `config.d` is mounted `:ro` into the dispatcher and the api.
- **The token's homes.** `scripts/configure.sh` generates the token. The
  root `README.md` *4. Bring up the stack* names it
  (`docs/learnings/an-env-key-only-an-adr-names-is-invisible.md`).
- **`pyproject.toml`'s `include`** gains `config_writer*`.

**Done when.**

- The writer's tests cover each of these:
  - a save, then a reset, each leaving the right file, `.prev` and log line;
  - a 409 on a stale revision;
  - a 422 carrying `load_config`'s message, with the files unchanged;
  - a 400 on a read-only, credential or unknown key;
  - a repairing save accepted while the file on disk is invalid;
  - `GET` answering 200 with `error` set when the merge fails;
  - a 401 without the token;
  - the rollback when appending the log fails;
  - startup refused without the token.
- `tests/integration/test_compose_invariants.py` asserts each of these, for
  both deployments:
  - the writer has no docker socket;
  - its only writable mount is `config.d`;
  - it is not on `ia_harness_net`;
  - its token reaches no service but the writer;
  - the dispatcher and the api mount `config.d` `:ro`.
- `python3 -m pytest` is green.

## 3. The console's settings screen

**Surface:** one screen, the Role models screen at
`front/src/routes/models.tsx`. It is rewired from its mock to the writer,
together with the console server half's forward that it reads through.
Reads `GET /settings` and the save and reset routes from task 2.

**The forward.**

- `front/src/server.ts` intercepts `/settings` and `/settings/<key>` and
  forwards `GET`, `PUT` and `DELETE` to `CONFIG_WRITER_URL` (default
  `http://127.0.0.1:8790`). It sends `Authorization: Bearer
  <CONFIG_WRITER_TOKEN>` and `X-Actor: operator`. Neither variable takes a
  `VITE_` prefix.
- The api's `FORWARDED` list does not change.
- A missing `CONFIG_WRITER_TOKEN` does not stop the console from booting.
  The forward answers 503 with a JSON body naming the variable.

**What the screen shows.**

- **Title and nav.** The title and the nav label become *Settings*. The
  route and its chord stay.
- **The table.** One row per key in `GET /settings`, in the table's order.
  Each row shows the key, the effective value, the source (`config.yaml`,
  `override` or `default`), the class and the reason.
- **Editable rows** get a control, chosen by the key's kind:
  - a boolean is a switch;
  - an integer is a number field;
  - an effort is a select over the five levels;
  - a model is a text field whose suggestions are the model IDs already in
    the base and the overrides. No list is invented here. That answers
    `front.md`'s "where the model list comes from";
  - `role_model` and `role_effort` are one line per role, with *(default)*
    taking the role out of the map, saved as the whole map;
  - `fallback_roles` is one checkbox per role;
  - `primary_account` is a select over the configured accounts.
- **Save and reset.** Each editable row has a *Save*. An overridden row
  also has a *Reset*, which sends `DELETE`.
- **Read-only and credential rows** have no control. A credential row says
  that renewing it belongs to the Tokens screen.
- **The note.** It is always shown above the table: *A change applies to
  the next dispatcher run. A task already running keeps the settings it
  started with. The Pool screen's thresholds follow at the api's next
  restart.*
- **Recent changes.** A table under the main one holds the log lines served
  in `recent_changes`, newest first. Each line shows its time (absolute, per
  `docs/ui.md`), the actor, the key, the operation, and the old and new
  values.

**States, in `docs/ui.md`'s terms.**

- *Loading* is per page.
- *Broken, unreachable:* the forward answers 503, or the writer cannot be
  reached or times out. The page shows an `ErrorState` naming the service
  and the variable, and **no controls are drawn**, per tier 3's floor: no
  control that is not wired.
- *Broken config:* `error` is non-null. An error-tone `Banner` carries the
  message verbatim and says the dispatcher refuses every verb until it is
  fixed. The table is still drawn, with *Reset* available, because a reset
  is how the operator repairs it.
- *No overrides:* this is not an empty state. Every row is drawn with its
  `config.yaml` or default source, and the recent-changes table shows its
  own `EmptyState` ("no change has been saved from the console").
- *A refused save:*
  - On a 422, the message is shown under the row and the value stays in
    the control, unsaved.
  - On a 409, a banner says the settings changed since the page loaded.
    The page refetches, and the edit is dropped.
- *Stale:* there is no poll. The page refetches after every save or reset.
  The revision check on save is what catches a change made elsewhere.

**Done when.**

- Route tests through `front/src/test/render-route.tsx` cover each of
  these:
  - the table drawn from a served payload;
  - the source column;
  - no controls on read-only and credential rows;
  - the unreachable state with no controls;
  - the broken-config banner with *Reset* present;
  - a save sending the revision;
  - a 422 message shown under its row;
  - the empty recent-changes state.
- `forward.test.ts` covers the new interception, including the 503 when the
  token is unset.
- `cd front && bun run typecheck`, `bun run test` and `bun run lint` are
  green.
- `front/AGENTS.md` and `front/README.md` name the two variables.

This screen and its forward may be too large for one cycle. If so, that
task's arquitecto may split it into a read-only screen first and the
controls second.

## 4. Credentials and the login (named, not designed here)

**Surface:** the Tokens screen and tier 3's queue. This is the Tokens task
of `docs/plans/front.md`. It covers renewing the GitHub token at
`git_credentials_path`, the deploy keys under `project_keys_dir`, and the
Claude login: `claude auth login` run in an account's container, the URL
shown, and the code pasted back. It reaches containers and credential
files, so it runs through tier 3's queue and a worker, never a docker
socket in a web process. It does not use the writer of task 2, and the
writer never holds a credential.

**Done when** its own design task has written its ADR. That design has to
cover the queue, the worker, and how the console sees whether a credential
exists. Whether `update_project_before_task` becomes editable waits on
that. It does not wait on tasks 1–3 and does not block them.
