# docker/agent/Dockerfile
# Build context: repo root (the hook scripts under hooks/ are copied in), e.g.
#   docker build -f docker/agent/Dockerfile -t ia-harness-agent .
FROM docker:27-cli AS docker-cli
FROM oven/bun:1.3.12 AS bun
# Node 24, not 20: the test gate runs front/'s vitest under this node, and the
# jsdom it pulls in (30.x) refuses anything below 22.22.2 — on 20 every test
# file fails to load, which the gate would read as a red suite on every task.
# bookworm is spelled out so the Debian the comments below rely on does not
# move with the tag.
FROM node:24-bookworm-slim

# python3-requests comes from apt: it is what /usr/bin/python3 needs for the
# observability hook, which runs as bare `python3` and must keep working even
# if the venv below is ever bypassed. openssh-client is for a project's deploy
# key (docs/plans/projects.md, Decisions 2): git fetches over SSH with
# GIT_SSH_COMMAND naming the key, and the host key it trusts is pinned below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        openssh-client \
        python3 \
        python3-requests \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# This harness is now one of the projects its own agents work on, and its
# docs/README.md declares `python3 -m pytest` as the test command — so a role
# working on it needs this repo's runtime dependencies importable, pytest
# included. A venv rather than `pip install --break-system-packages`: the base
# is Debian bookworm, whose system Python is PEP 668 externally-managed, and
# apt cannot satisfy the floors pyproject.toml declares anyway (bookworm ships
# Flask 2.2 against flask>=3.0, pytest 7.2 against pytest>=8.0). Overriding
# PEP 668 would leave pip and dpkg both owning the same names; a venv owns
# its own.
#
# The versions are pyproject.toml's, spelled out rather than installed from
# it: the image must not depend on a checkout of the project it builds for.
# Keep the two in step by hand; tests/ has no gate that would catch drift.
#
# PATH-prepended, not activated, because nothing here runs a login shell:
# `docker exec` sessions and the entrypoint both inherit image env only. That
# makes the venv's python3 the one every bare `python3` resolves to.
ENV VIRTUAL_ENV=/opt/ia-harness/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
RUN python3 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir \
        "pyyaml>=6.0" \
        "flask>=3.0" \
        "requests>=2.31" \
        "pytest>=8.0"

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

# github.com's host keys, pinned in the system-wide known_hosts so ssh never
# has to accept one on first use, which would trust whoever answers first. The
# dispatcher's ssh command also sets StrictHostKeyChecking=yes and points the
# per-user file at /dev/null, so this file is the only thing ssh trusts.
COPY docker/agent/github_known_hosts /etc/ssh/ssh_known_hosts

# bun, for the gate that runs front/'s typecheck, test and lint scripts (see
# docs/README.md's `install:`, `test:` and `lint:`). front/ is a bun project —
# bunfig.toml holds the release-age floor and the lockfile is bun's text one —
# so `npm install` would ignore both. Copied as a binary from the official
# image rather than curl-piped: the version is pinned by tag, and the binary
# is all bun is. Keep it in step with the version front/ is developed on.
#
# The install cache lives on the projects mount, beside the worktrees: bun
# hardlinks out of its cache when the two share a filesystem and copies when
# they do not, which is ~450MB per worktree for front/ alone. It also outlives
# a recreate, so the first round after one does not refetch everything. Image
# env, for the same reason as CLAUDE_CONFIG_DIR below: `docker exec` sessions
# inherit nothing an entrypoint exports.
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
RUN ln -s /usr/local/bin/bun /usr/local/bin/bunx
ENV BUN_INSTALL_CACHE_DIR=/data/projects/.cache/bun

# Every git command the agents run works on /data/projects, a bind mount owned
# by the host user, so without this each one dies with `fatal: detected
# dubious ownership` — worktree add included, which is the first thing a role
# needs. This image's git is 2.39.5, where safe.directory matches only an
# exact path or the literal `*`; trailing-`/*` prefix matching landed in 2.46,
# so `/data/projects/*` would silently not match here. `*` is the honest
# choice for a single-purpose container that runs as root and only ever
# touches repos under that one mount.
#
# The identity is a fallback so a commit can never die with `Author identity
# unknown`. It is deliberately generic and unroutable (.invalid is reserved by
# RFC 2606): real attribution comes from the dispatcher passing
# `git -c user.name=… -c user.email=…` per commit, so this name only surfaces
# when something commits outside that path. System scope, not --global, so it
# holds no matter what HOME a `docker exec` session gets, and still loses to
# both the per-commit -c and any repo-local config.
RUN git config --system safe.directory '*' \
    && git config --system user.name 'ia-harness agent' \
    && git config --system user.email 'agent@ia-harness.invalid'

# A headless Chromium for the phases' screen walks (docs/decisions.md ADR 43,
# docs/plans/browser.md). The image carries only the Node Playwright client and
# Chromium's system libraries; the browser build itself lives in
# <repo-root>/.data/ms-playwright on the host, filled by the
# `playwright-install` service in docker/compose/docker-compose.yml and mounted
# read-only at /ms-playwright in every agent.
#
# PLAYWRIGHT_VERSION is the one place the version is declared. A client looks
# for exactly the browser revision it was released with, so the install
# service runs this image's own `playwright` rather than a client of its own:
# the revision it downloads is the one this client asks for, by construction.
# Bumping it is a rebuild of this image and one run of that service; read-only
# in the agents, a mismatch fails loudly ("Executable doesn't exist at ...")
# instead of downloading a second browser.
#
# PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD on the npm line only, never as image env, so
# the install service's explicit `playwright install` is not told to skip.
# `install-deps chromium` is Playwright's own apt-get list for this Debian; the
# libraries stay in the image because a volume cannot provide Debian packages.
#
# PLAYWRIGHT_BROWSERS_PATH is image env for the same `docker exec` reason as
# CLAUDE_CONFIG_DIR below, and the compose mounts name the same path.
# NODE_PATH lets a phase's CommonJS script `require('playwright')` from the
# global install; an ES module ignores NODE_PATH and imports it by path,
# /usr/local/lib/node_modules/playwright/index.mjs.
ARG PLAYWRIGHT_VERSION=1.56.1
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -g playwright@${PLAYWRIGHT_VERSION} \
    && playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV NODE_PATH=/usr/local/lib/node_modules

# playwright-cli, the shell front end the `playwright-cli` skill drives
# (skills/playwright-cli, docs/plans/browser.md option B). Pinned like
# everything else in this image, never @latest. It brings its own Playwright
# client, so it is pointed at the browser the mount above already provides
# instead of downloading one: PLAYWRIGHT_MCP_EXECUTABLE_PATH names that
# build's binary by revision (1194 is Chromium 141, the one PLAYWRIGHT_VERSION
# 1.56.1 asks for), so the two move together. The sandbox is off because the
# agent runs as root, where Chromium refuses to start with it. Headless and an
# in-memory profile are the CLI's defaults and are left that way. Auto-named
# output goes to /tmp, out of the worktree a phase commits from.
ARG PLAYWRIGHT_CLI_VERSION=0.1.22
RUN npm install -g @playwright/cli@${PLAYWRIGHT_CLI_VERSION}
ENV PLAYWRIGHT_MCP_EXECUTABLE_PATH=/ms-playwright/chromium_headless_shell-1194/chrome-linux/headless_shell
ENV PLAYWRIGHT_MCP_SANDBOX=false
ENV PLAYWRIGHT_MCP_OUTPUT_DIR=/tmp/playwright-cli
ENV PLAYWRIGHT_MCP_VIEWPORT_SIZE=1280x800

# The dispatcher parses this CLI's --output-format json shape, /usage text,
# and rate-limit messages, so bump this version deliberately and re-verify.
ARG CLAUDE_CODE_VERSION=2.1.296
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Without this the CLI's background auto-updater runs `npm install -g` inside
# the running container, silently replacing the pinned version above until
# the next recreate; an update cut off mid-install (the session ends) leaves
# no `claude` on PATH at all. Upgrades go through a rebuild with a new
# CLAUDE_CODE_VERSION instead. Image env so `docker exec` sessions see it.
ENV DISABLE_AUTOUPDATER=1

# Observability hook: emit_event.py POSTs each Claude Code hook event to the
# collector (COLLECTOR_URL / SOURCE_APP are set per agent service in
# docker/compose/docker-compose.agents.yml); install_settings.py registers it
# in /root/.claude/settings.json (the shared volume, symlinked into
# CLAUDE_CONFIG_DIR by the entrypoint) at container start (see entrypoint.sh).
COPY hooks/emit_event.py /usr/local/lib/ia-harness/emit_event.py
COPY hooks/install_settings.py /usr/local/lib/ia-harness/install_settings.py
# cap_subagent_return.py is the SubagentStop return cap install_settings.py
# registers beside it (ADR 510).
COPY hooks/cap_subagent_return.py /usr/local/lib/ia-harness/cap_subagent_return.py

# The vendored role skills (see skills/README.md). Baked into the image, not
# installed into the shared /root/.claude, so the set a role gets moves only
# when the image moves — claude_shared tracks no version, and a skill
# installed there would drift inside running containers exactly the way the
# CLI auto-updater above does. The dispatcher delivers them per call, one
# --plugin-dir per skill, from dispatcher/role_skills.py; SKILLS_ROOT there
# is this destination path, and a test asserts the two still agree. The whole
# directory is copied, loose NOTICE.md and README.md included: the upstream
# licences (MIT, and Apache-2.0 for playwright-cli) require their notices to
# travel with the copies, and --plugin-dir ignores files that are not plugins.
COPY skills /opt/ia-harness/skills

COPY docker/agent/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# The Claude Code config home: must be image env, not something the
# entrypoint exports, because `docker exec` sessions (the dispatcher's
# `claude -p` and the operator's `/login`) inherit container env but never
# variables an entrypoint sets at runtime. Each agent service mounts its own
# claude_creds_<account> volume here; /root/.claude (claude_shared) holds the
# names every account shares, symlinked in by the entrypoint.
ENV CLAUDE_CONFIG_DIR=/root/.claude-account

WORKDIR /data/projects
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
