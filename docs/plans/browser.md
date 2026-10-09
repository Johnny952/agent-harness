# Browser — giving the phases a headless Chromium, shared from one volume

**Status:** proposal, nothing built. Written 2026-10-09 by the operator, after
walking `docs/ROADMAP.md` **V0.6e** with a Playwright script from the host
(the Results log row of that date; scripts and screenshots in
`.data/verify/v06e/`). That walk took about forty lines of Python and found a
real defect, the detail screen's error state flickering with "Reading task…",
which no phase could have seen. This page proposes letting the phases do the
same, and lists the ways an agent can drive Playwright.

## The premise it changes

`docs/ROADMAP.md`'s V0.6e block says: "What no phase can do is open a browser
or bring up the container half, which is a human's row". That is true of the
container half, and stays true: docker is refused in a phase
(`T-010-docker-is-refused-in-a-phase.md`). It is true of the browser only
because `docker/agent/Dockerfile` ships none. A browser is a binary, not a
privilege, so the half of every console check that is "open this screen and
read it" can move to the phase that wrote the screen.

What still needs a human or the operator after this change:

- anything that stops or starts a container (V0.6e step 5's literal "stop the
  api" is one);
- anything against the real api with its bearer, which a phase must not hold;
- a judgement about how a screen *looks*, as opposed to what it says.

## Proposal

### 1. Chromium comes from a shared read-only volume, not from the image

- A host directory, `.data/ms-playwright/`, holds the browser builds. It is
  filled once by a one-shot compose service under its own profile, for
  example `playwright-install`, that runs
  `playwright install chromium-headless-shell` with
  `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`. The operator runs it, and runs it
  again when the pinned Playwright version changes.
- Each agent service mounts it read-only:
  `../../.data/ms-playwright:/ms-playwright:ro`, with
  `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` in its environment.
- The image installs only the client, pinned, with
  `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, plus Chromium's system libraries
  (`playwright install-deps chromium`, or the equivalent `apt-get` list). The
  libraries stay in the image: they are Debian packages tied to the
  `node:24-bookworm-slim` base, and a volume cannot provide them.

**Why not mount the host's `~/.cache/ms-playwright`.** It was the first idea
and it does work for one revision, but three things argue against it:

- Playwright garbage-collects that directory. When a host-side install no
  longer references a revision, the next `playwright install` on the host
  deletes it, and the agents' pinned revision can vanish under them.
- It sits in one user's home, outside the repo's `.data/`, where every other
  mount of this harness lives.
- On this host it holds three revisions (1217, 1228, 1234) and their headless
  shells, about 2 GB. The agents need one headless shell, about 260 MB.

**What the volume saves, honestly.** Containers of one image already share its
layers, so cuenta1 and cuenta2 would not each pay for a baked-in browser. The
saving is elsewhere:

- every rebuild of `docker/agent/` that touches a layer above the browser
  would otherwise leave about 260 MB behind in a dangling image;
- the browser's version moves independently of the image, so bumping it is a
  one-shot run rather than an image rebuild;
- the agent image stays at its current size for every phase that never opens
  a browser, which is most of them.

**The version contract.** A Playwright client looks for exactly the browser
revision it was released with, and `PLAYWRIGHT_BROWSERS_PATH` only says where
to look. The client version in `docker/agent/Dockerfile` and the one in the
`playwright-install` service must be the same pinned string, declared once (a
build arg read by both). Read-only is a feature here: a mismatch fails loudly
("Executable doesn't exist at …") instead of downloading a second browser
into the volume.

### 2. Running Chromium inside the agent container

- **Sandbox.** The agents run as root, and Chromium refuses its sandbox as
  root. Pass `--no-sandbox` (Playwright's `chromiumSandbox: false`). The
  container is already the isolation boundary.
- **`/dev/shm`.** Docker's default is 64 MB, which crashes Chromium on long
  pages. Either `shm_size: "1gb"` on the agent services or
  `--disable-dev-shm-usage`. The second needs no compose change.
- **Memory.** The agents have `mem_limit: "4g"`, and D8 measured the `front/`
  gate at 735 MiB peak. A headless shell with one page is a few hundred MB on
  top of the dev server; this should be measured once, as D8 was, before the
  limit is trusted.
- **File watching.** The host walk needed `CHOKIDAR_USEPOLLING=true` for
  `bun run dev` because inotify ran out of watches (ENOSPC). A container shares
  the host kernel's inotify limits, so a phase starting the dev server should
  set it too.

### 3. What a phase walks against

A phase must not hold the api's bearer, and should not depend on the api
container being up. So the phase walk is against the dev server with
Playwright's `page.route("**/api/**", …)` answering every read from a fixture
the phase writes. That is what V0.6e step 5's region check needed anyway:
today's walk had to fail `/api/learnings` alone, with the rest of the api up,
and only a route mock can do that.

This splits a console check into two rows:

- **the phase's row**: the screen against fixture answers, including empty,
  error and over-cap cases that real data may not reach (V0.6e step 3's banner
  was unreachable with 27 entries against a cap of 40);
- **the operator's row**: the same screen against the live api, which stays
  out of reach of a phase.

### 4. How a phase-made walk is recorded

- The Results log row names who walked it: "walked by T-0xx's implementador,
  headless, against route fixtures". Not "PASS" in a column the reader takes
  for a human's.
- Screenshots and the script go under `/data/.hive/tasks/T-0xx/verify/`, not
  into the repo. The row cites them.
- The revisor reruns the script; it does not re-read the screenshots. A walk
  is a script, so it is reproducible, and that is the point of moving it.

### 5. Permissions

The phases' `settings.json` allowlist needs the one command the walk runs,
for example `Bash(playwright-cli:*)` or `Bash(node /data/…/walk.mjs)`. That is
the operator's edit, outside any task, like the `bun` grants were. No new
network access is needed: the dev server is on the container's own loopback.

## ADR sketch

To be appended to `docs/decisions.md` by the task that builds this, not here.

> **ADR n — phases get a headless Chromium from a shared read-only volume**
>
> *Context.* Console checks need a browser, and the agent image has none, so
> every one has been a human's or the operator's row, and V0.6e's flicker was
> found only when the operator walked it by hand.
>
> *Decision.* The agent image ships a pinned Playwright client and Chromium's
> system libraries. The browser build lives in `.data/ms-playwright/`, filled
> by a one-shot `playwright-install` service and mounted read-only into every
> agent at `/ms-playwright`. Phases walk console screens against the dev server
> with `/api/**` answered by route fixtures; walks against the live api stay
> the operator's.
>
> *Consequences.* The image grows by Chromium's libraries only. A version bump
> is one build arg plus one run of the install service. A phase can now claim a
> console check, and the Results log says it was a phase. The bearer stays out
> of the phases. Container checks remain a human's.

## Skills: the ways an agent can drive Playwright

Checked 2026-10-09. None is installed on this host; the only mentions under
`~/.claude/` are recommendations inside other plugins.

| Option | What it is | For this harness |
|---|---|---|
| **A. Plain script** | The agent writes a Playwright script (Python or Node) and runs it with Bash. What V0.6e used. | No dependency beyond the client. Fewest tokens: only what the script prints enters the context. The script *is* the evidence, and the revisor can rerun it. The agent has to know the API. |
| **B. `playwright-cli` + its skills** (`microsoft/playwright-cli`, `npm i -g @playwright/cli`, then `playwright-cli install --skills`) | A CLI whose commands drive a browser session, plus skill files that teach Claude Code to use it. Microsoft recommends it over the MCP for coding agents, because it does not load tool schemas and accessibility trees into context; screenshots are saved to disk and the path is returned. | The best fit for interactive exploration inside a phase. Skills would go in `claude_shared` (`/root/.claude/skills`), so every account gets them. One more global npm package to pin in the image, and the session state has to survive between Bash calls. |
| **C. Playwright MCP** (`microsoft/playwright-mcp`, `claude mcp add playwright npx @playwright/mcp@latest`; flags `--headless`, `--isolated`, `--executable-path`, `--no-sandbox`) | An MCP server exposing browser actions as tools, reading pages as accessibility snapshots. | The most capable for long exploratory loops, and the most expensive: every action returns a snapshot into context, which `docs/plans/token-economy.md` shows is the cost that compounds. `@latest` via npx also contradicts this harness's pinned-version rule. Not for phases. |
| **D. Anthropic's `webapp-testing` skill** (`anthropics/skills`, Apache-2.0) | A skill that tells the agent to write Python Playwright scripts, with a `with_server.py` helper that starts and stops the dev servers around them. | Option A with instructions and a server-lifecycle helper. Needs Python Playwright in the image rather than Node, which the image does not have. Worth reading for its recon-then-script workflow even if not installed. |

**Recommendation.** Option A for the checks a phase records, because a script is
reproducible and the revisor can rerun it. Option B as the skill that helps a
phase explore a screen before it writes that script. Option C stays off the
phases, for its token cost. Option D is worth adopting only if the image gains
Python Playwright for another reason.

Before any of B–D is installed, read its `SKILL.md` and pin its version: a skill
is a prompt the phases will obey, so it is part of the harness, not a tool
beside it.

## Order of work

1. The one-shot install service and the read-only mount, with the version
   declared once. Measure the agent's memory with Chromium running, as D8 did.
2. The client and system libraries in `docker/agent/Dockerfile`; a smoke check
   that a phase can screenshot `about:blank`.
3. The allowlist entry, by the operator.
4. One console task whose done-means includes a phase walk against route
   fixtures, to test the recording rules on a real row.
5. Then, separately, whether to install option B's skills.

Each step is one surface, per `docs/plans/token-economy.md` P4.
