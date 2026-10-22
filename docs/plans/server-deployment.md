---
status: building
approved: 2026-10-11
built: null
supersedes: []
---

# Server deployment — the console as the only door, accounts added from it

**Status:** design, accepted in outline by the user on 2026-10-11, not built.
It records the gaps the main thread listed that day, after the main-thread,
notifications and command-requests plans, and the user's direction on them:
the end state is the harness on a server, run as much as possible from the
console, including adding a new Claude account from the console itself. It
builds on `docs/plans/main-thread.md`, `docs/plans/notifications.md`,
`docs/plans/board-service.md` and `docs/plans/balancer.md`.

## The goal

The person opens one URL, signs in, and does everything they now do over SSH
or in the host session:

- talk to the main thread;
- start and review work;
- answer command requests;
- add, sign in, pause and remove the Claude accounts the pool runs on.

The server's shell is for installing the harness and for repairing it,
nothing routine.

## What exists today

- **Auth is a shared password.** `observability/auth.py` accepts Basic auth
  (a username and a sha256 hash in the compose file) and a service bearer
  token (ADR 7). Its docstring says it plainly: "there are no sessions, no
  cookies and no users". There is no TLS in the stack: plain HTTP on the
  host.
- **A server deployment is half there.** `docker/compose/docker-compose.coolify.yml`
  is a single-file variant for a Coolify resource, which builds every image
  and creates the volumes on first `up`. Coolify's proxy can give it HTTPS.
  The dispatcher is behind a compose profile, run by hand.
- **Accounts are fixed at install time.** An account is:
  - a `name` and `container` in `accounts[]` of `config.yaml`, read when
    the dispatcher starts;
  - an `agent-<name>` and `dind-<name>` service pair written by hand into
    the compose files (`scripts/configure.sh` says it does not edit them);
  - a `claude_creds_<name>` volume, from `scripts/setup_volumes.sh` or
    Coolify's first `up`;
  - a login done by hand: `docker exec -it agent-<name> claude`, then
    `/login` (`docs/ROADMAP.md` V0.4).
- **The console reads accounts and cannot change them.** `/pool` reads
  `GET /api/accounts`. Nothing writes an account.
- **The console is a desktop screen.** The routes in `front/src/routes/`
  have almost no responsive classes (checked 2026-10-11), and the screens
  that pushes will open, Approvals and the diff, are wide tables.

## Decisions

### 1. Sign-in is a real login, with sessions, behind HTTPS

A console that spends quota, approves commands and talks to a model needs
more than a shared password sent with every request. So:

- **People.** A `users` store, in the board service's own state (not the
  hive, which agents read). Each user has a name, a password hash
  (argon2 or bcrypt, not a bare sha256) and a TOTP secret.
- **Two factors.** A password and a TOTP code, required from the first user
  on. Passkeys (WebAuthn) are the better second factor and come after, as an
  option.
- **Sessions.** A signed, `HttpOnly`, `Secure`, `SameSite=Strict` cookie,
  with an idle timeout and a fixed lifetime. Every write also checks a CSRF
  token. The console's server half keeps the bearer token for the api, as
  ADR 7 has it; the browser never sees it.
- **Throttling.** Failed sign-ins are slowed per user and per address, and
  locked for a while after several. Each sign-in, success or not, is
  written to an audit log.
- **HTTPS only.** TLS ends at the server's proxy (Coolify's, or a Caddy
  service in the plain compose). The api and the board service listen only
  on the internal network. Plain HTTP stays allowed on `localhost`, for
  development.
- **Basic auth retires** once the login works, behind an ADR that supersedes
  the human half of ADR 7. The service bearer token stays.

The first user is created from the server's shell, once, by a command that
prints a one-time setup link. That is the one routine use of the shell this
plan keeps, because whoever creates the first user owns the harness.

### 2. Who may give which verdict

One person today, but the store is built for more. Each user has one of two
roles:

| Role | May |
|---|---|
| `owner` | everything, including accounts, users and the allowlists of `command-requests.md` |
| `reviewer` | read, comment, and approve or ask for changes in review (`review.md`) |

A reviewer's *approve* is a recommendation, not the merge. Settled by the
user on 2026-10-11:

- **A reviewer approves.** The task moves to *Awaiting owner's approval*,
  with the reviewer's verdict and comments on it. Nothing runs, no merge
  starts, and no quota is spent.
- **The owner's approval starts the merge flow.** It is the human verdict
  C-12 speaks of, and it authorizes what it starts.
- **Asking for changes** is open to either role. From a reviewer it is also
  a recommendation the owner confirms, because a new implementing round
  spends quota.
- **The owner may approve directly**, with no reviewer verdict, as today.

Starting work and talking to the main thread spend quota, so they are the
owner's until the user decides otherwise. Every verdict records its `by`,
which until now has been implied.

### 3. Adding an account from the console

An account becomes a record the console writes, not a config entry and a
compose edit. The flow, as the person sees it, on `/pool`:

1. **Add account.** The person types a name. It is checked as
   `configure.sh` checks it today, and it must be new.
2. **The containers are made.** The worker creates the `claude_creds_<name>`
   volume and the `agent-<name>` and `dind-<name>` pair. It does this from
   one fixed template, the same image, mounts, limits and network as the
   existing pairs. The request carries only the name. No image, mount or
   flag comes from the console, so a request can make an account and
   nothing else.
3. **Sign in.** The worker starts the CLI's login in the new agent
   container, in a pseudo-terminal it keeps. It reads the sign-in URL the
   CLI prints and the console shows it as a link. The person opens it,
   signs in to Claude in their own browser, and pastes the code they get
   back into the console. The worker writes the code into the waiting
   login, and nothing else. The credentials land in that account's volume,
   as today. The code is not logged or stored, and the console clears it
   once sent.
4. **Check.** The worker runs `/status` in the container (no model turn) and
   the console shows which Claude account it reports. The person confirms
   it is the one they meant, because signing two containers in to the same
   Claude account defeats failover (V0.4).
5. **Into the pool.** The account starts *paused*. The person resumes it
   when ready, and the picker takes it from its next pick.

The same login step is how an expired login is renewed: *Sign in again* on
the account's card.

**To verify before building:** exactly what the CLI prints and expects on
the login flow in a container with no browser, in the version the agent
image ships. If the CLI offers a long-lived token for headless use, that
token is the alternative, pasted the same way but more sensitive, since it
is the credential itself rather than a one-time code. Which one is used is
decided at step 4 of the build, under an ADR.

### 4. Accounts are a record the dispatcher reloads

- **The record.** `accounts[]` moves from `config.yaml` to the dispatcher's
  state, one file per account:
  - `name`, `container`, `dind`, `added_by` and `added_at`;
  - `status`: `signing_in`, `paused`, `active` or `removed`.

  `config.yaml` stays the seed: on first start, its accounts are imported,
  and a config with accounts keeps working for a host install. It also
  gives `command-requests.md` its `accounts[].dind`.
- **The pool reloads.** The dispatcher rereads the accounts at each pick,
  not only at start, so an account added or paused applies without a
  restart. The primary override from `main-thread.md` lives beside it.
- **Pause and remove.** *Pause* takes an account out of the pick and leaves
  running phases alone. *Remove* is two steps:
  - the account is paused, its containers are stopped and removed, and the
    record is marked `removed`. Its volume stays, so a mistake can be
    undone by adding the same name again;
  - deleting the volume, which holds the login, is a separate button with
    its own confirmation, because it cannot be undone.
- **Never touched.** The flow creates and removes only `agent-<name>`,
  `dind-<name>` and `claude_creds_<name>` for names the records hold. It
  never touches `claude_shared`, another account's containers, or any
  container the records do not name.

### 5. The socket stays with the worker

Making containers needs the docker socket. The api and the board service
still never get it (`board.md`, *Actions go through a queue, not a socket*).
Adding, signing in, pausing and removing are queue actions, run by the
worker like a turn or a start: `account_add`, `account_login`,
`account_pause`, `account_remove`, `account_delete_volume`. The login's
pseudo-terminal lives in the worker, and the console reaches it only
through those actions and the URL and status the worker writes back.

**Decided 2026-10-19** by T-097 (`docs/decisions.md` ADR 142): the worker is
a `worker` compose service built from the dispatcher's image, the one
long-running service with the socket, and one at a time under a `flock`. The
queue is JSON files under `<state_dir>/actions/` (`pending/`, `claimed/`,
`done/`), created by the board service from the console's routes and moved
only by the worker; not the hive, which the agents mount read-write. The
account kinds above are named in its record and each is added under its own
ADR. Step 3 of *The order across the plans* is that ADR's split.

### 6. The phone is a first-class place for a few screens

Pushes (`notifications.md`) bring the person to the console on a phone. So
a few screens are built to work at phone width, and the rest only have to
not break:

- **Fit for the phone:** the inbox, a task's page, the diff with its line
  comments, Approvals (a command request, a verdict), the main thread, and
  the pool.
- **The diff on a phone** shows one file at a time, unified, never side by
  side, with long lines wrapped and the verdict buttons at the foot.
- **Desktop first, and must not break:** the board's columns, the roadmap,
  the token and debt screens.

Each fit is part of that screen's own front task, not a separate pass.

### 7. The shell keeps only installing and repairing

On a server, the shell is for:

- installing the harness;
- creating the first user (*Decisions*, 1);
- restoring a backup;
- recovering when the console is down.

Everything a person does day to day has a place in the console. A new
routine that needs the shell is a gap to record here.

### 8. Backups hold the harness's records, never a credential

Settled by the user on 2026-10-11, who confirmed the same day that the users
store stays out, so a restore means recreating the first user. What holds
state, and what a backup does with it:

| State | Where | In the backup |
|---|---|---|
| The hive: board, tasks, comments, thread records, `conversations/*.jsonl` | the hive volume | **yes**: it is the project's history and cannot be recreated |
| The dispatcher's state: cycles, the usage log, the account records | `/state` | **yes**: the cost and cycle history is lost without it |
| The users store | the board service's state | **no**: it holds password hashes and TOTP secrets, which are credentials |
| `claude_creds_<name>` | one volume per account | **no**: the Claude logins. After a restore each account signs in again from `/pool` (*Decisions*, 3) |
| `claude_shared` | the shared volume | **no**: large, and it holds every phase's transcript. A thread's history survives in the hive, so a restore costs only the model's memory, which is a new segment with its separator (`main-thread.md`, *Decisions*, 5) |
| `.data/projects` | the host's disk | **no**: the clones and task branches are on GitHub (C-5 as amended). Only a worktree mid-phase is lost |

- **Where.** A directory named by an environment variable, `BACKUP_DIR`,
  on the server by default. An off-server destination (a bucket, another
  host, Coolify's backup target) is deferred, and far: everything that
  cannot be recreated is small, and the code is already on GitHub.
- **When, and how many.** Nightly, and once before each merge. A fixed
  maximum of copies, set by a second variable; past it, the oldest is
  deleted first.
- **Consistency.** The copy is taken between phases, or with the
  dispatcher's writes paused for its duration, so the hive and the state
  agree.
- **Restore** is a shell command (*Decisions*, 7): it stops the dispatcher,
  puts back the hive and the state, and starts it again. Then each account
  signs in again, and the owner recreates the first user (*Decisions*, 1),
  since the users store is not in the backup. Kept to that and no more.
- **A dead container is not a restore.** The volumes outlive the
  containers, so a container that dies is recreated over the same volumes,
  sessions and logins included. The backup is for a lost or corrupted
  volume or disk.

### 9. The dind runs in one of a few modes, chosen per account

Settled by the user on 2026-10-18. Today every account's dind runs under
`runtime: sysbox-runc` (`docker/compose/docker-compose.agents.yml`,
`docker-compose.coolify.yml`), and the README makes sysbox a requirement. A
server the harness is deployed to may not allow installing a runtime, so
sysbox becomes the preferred mode, not a requirement.

Why a dind at all: a phase that needs containers (the project's compose,
an image build, an integration test against a real database) needs a docker
daemon. The host's socket is not one to give it: whoever holds it is root on
the host and can see and stop every container, the dispatcher, the api,
`postgres-db` and the other accounts' agents among them. A dind gives each
account a daemon of its own, with its own images, networks and volumes, that
the agent reaches through `DOCKER_HOST` and that sees nothing else.

The modes, one per account:

| Mode | What runs | When |
|---|---|---|
| `sysbox` | the dind under `sysbox-runc`, no `--privileged` | the default, wherever sysbox can be installed |
| `remote` | no local dind; `DOCKER_HOST` points at a daemon on another machine or VM set aside for that account | a server where no runtime can be installed but another host can run docker |
| `none` | no dind and no `DOCKER_HOST` | projects whose work needs no containers; a command request of kind `dind` or `service` is refused with its reason, as ADR 118 already does for an account with no `dind` key |
| `privileged` | the dind with `privileged: true` | an explicit opt-in only, never a default and never picked by the installer on its own. It is close to root on the host, so choosing it names that in the config and the README warns of it |

- **Where it is set.** In the account's record (*Decisions*, 4), seeded from
  `config.yaml`, beside `dind`. The template of *Decisions*, 3 makes the
  pair that the mode names, and nothing else.
- **The installer checks.** `scripts/configure.sh` looks for `sysbox-runc`
  in `docker info`. With it, `sysbox` is offered; without it, `none` is, and
  `remote` and `privileged` only when asked for by name.
- **Never the host socket.** No mode mounts `/var/run/docker.sock` into an
  agent or a dind, and the command-requests denylist keeps refusing it
  (ADR 118).

## The order across the plans

Most of what these plans add needs the queue and the worker first. This is
the order that follows from their dependencies. ROADMAP Stage 2 still holds
the older list, and whether it is replaced by this one is the user's call
(`roadmap-view.md`, *Open questions*).

1. **No quota, no queue.** Can start now:
   - `command-requests.md` step 1, capturing the denials;
   - the board-service steps 1 to 3: the task module, the board seam
     removed, the epic format;
   - `roadmap-view.md`'s cheap form.
2. **The board service, reads, then writes.** Board-service steps 4 to 7.
3. **The queue and the worker.** The critical path. The `turn`, `start`,
   command and account actions all ride on it.
4. **Sign-in** (*Decisions*, 1 and 2). Before anything below is exposed
   beyond `localhost`.
5. **Accounts from the console** (*Decisions*, 3 to 5).
6. **Starting development** with its spending limit (board-service step 9),
   after its charter ruling.
7. **Review** (`review.md`), and **command requests** steps 2 to 6.
8. **The main thread** (`main-thread.md`): read-only first, then the
   composer after its charter ruling.
9. **Notifications** (`notifications.md`). The inbox can come earlier; the
   pushes need HTTPS, so they come after step 4.

Front work goes into each step, each behind its own ask for a build.

### The 2026-10-11 additions, by priority

Asked for by the user on 2026-10-11, to be taken in this order. None needs
the queue except the last, so they may run beside the list above:

1. **Models 5.5** (`models-5-5.md`): model IDs, explicit effort, the
   premature-stop paragraph. Changes the cost of every phase after it.
2. **run-skill-generator in mapping** (`skills-adoption.md`, *1*): how to
   build and run a target project, recorded once.
3. **The research role** (`research-role.md`).
4. **The E2E skill and gate** (`skills-adoption.md`, *2*).
5. **The design skill** (`skills-adoption.md`, *3*).
6. **Local backups** (*Decisions*, 8). The off-server destination is
   deferred further.
7. **OmniRoute** (`omniroute.md`), after sign-in and under an ADR.

## How it would be built

Each step is a single surface:

1. The users store, password and TOTP, the sessions, the CSRF check and the
   audit log, in the board service. An ADR supersedes the human half of
   ADR 7.

   **Built 2026-10-22** by T-146 (`docs/decisions.md` ADR 324):
   `board_service/signin.py`, installed by `create_app` when `BOARD_AUTH_DIR`
   is set. Stdlib scrypt and TOTP instead of argon2 and an opaque server-side
   session id instead of a signed cookie; the cookie, CSRF and role checks
   bind only requests that carry the cookie, so Basic auth stays until step 2.
2. The console's sign-in screen and the first-user setup command. A front
   task, behind its own ask for a build.
3. TLS: the proxy in the plain compose, and the Coolify notes in the README.

   **Built 2026-10-17** by T-062: a `caddy` service in the plain compose only,
   in front of the console, with its Caddyfile in `docker/caddy/Caddyfile`;
   `HARNESS_DOMAIN` and optional `ACME_EMAIL` choose HTTPS or plain localhost.
   In Coolify its own proxy does this and Caddy is not added; the README says
   so. See `docs/decisions.md` ADR 100.

   **Amended 2026-10-17** by T-063: Caddy now asks for Basic auth on every
   request (`CONSOLE_AUTH_USER`, `CONSOLE_AUTH_HASH`) and refuses its config
   with no hash. The console behind Coolify's proxy is still unauthenticated.
   Step 4 of *The order across the plans* replaces this. See
   `docs/decisions.md` ADR 103.

4. The account records, the seed from `config.yaml`, the reload at each
   pick, and pause. Under an ADR, which also settles the login method of
   *Decisions*, 3.
5. The worker's account actions: the template, the login's pseudo-terminal,
   the status check and removal.
6. The account screens on `/pool`. A front task, behind its own ask for a
   build.
7. The phone fits, inside each screen's own task (*Decisions*, 6).
8. The backup and restore commands, `BACKUP_DIR` and the copy cap
   (*Decisions*, 8).

   **Built 2026-10-22** by T-147 (`docs/decisions.md` ADR 327 and 328):
   `dispatcher/backup.py`, the `backup` and `restore` verbs, and
   `scripts/backup.sh` / `scripts/restore.sh`. A copy is a plain directory
   taken with every project lock held; the cap is `BACKUP_KEEP` (default 7).
   Restore syncs in place and sets aside what it replaces. Not built: the
   nightly and before-merge scheduling (the worker has no `BACKUP_DIR` mount,
   a compose change awaiting approval) and an off-server destination.
9. The dind modes (*Decisions*, 9): the per-account key, the compose
   variants for `remote`, `none` and `privileged`, the installer's check,
   and the README's requirement turned into a preference. Under an ADR.

## Open questions

- **The login method.** The one-time code through the worker, or a
  long-lived token pasted once (*Decisions*, 3). Verified on the CLI first.
- **Resource limits per account.** Each pair takes 2 CPUs and 4 GB today.
  Whether the console shows the server's room before adding an account, and
  refuses past it, is decided at step 5.
- **Backups.** Settled on 2026-10-11 (*Decisions*, 8): the hive and the
  dispatcher's state, never a credential, into `BACKUP_DIR` with a capped
  number of copies. The off-server destination is deferred.
- **More than one person.** The roles exist (*Decisions*, 2). Per-person
  notifications and read state come with them (`notifications.md`).
- **The reviewer role and C-12.** Settled on 2026-10-11 (*Decisions*, 2): a
  reviewer's approve waits for the owner's, and only the owner's starts a
  run.

## What it is not

Not a multi-tenant service: one harness, one owner, perhaps a few
reviewers. Not a way to run arbitrary containers from the console: only an
account's fixed pair. Not a place the Claude credentials pass through:
the console relays a one-time code to the login waiting for it, and the
login stays in the account's own volume.
