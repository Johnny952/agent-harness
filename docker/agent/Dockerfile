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
# if the venv below is ever bypassed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
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
# Flask 2.2 against flask>=3.0, pytest 7.2 against pytest>=8.0) and has no
# `mcp` at all — which dispatcher/vibe_kanban_client.py imports at module
# level, so the suite does not even collect without it. Overriding PEP 668
# would leave pip and dpkg both owning the same names; a venv owns its own.
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
        "mcp>=1.0.0" \
        "pytest>=8.0"

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

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

# The dispatcher parses this CLI's --output-format json shape, /usage text,
# and rate-limit messages, so bump this version deliberately and re-verify.
ARG CLAUDE_CODE_VERSION=2.1.273
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

# The vendored role skills (see skills/README.md). Baked into the image, not
# installed into the shared /root/.claude, so the set a role gets moves only
# when the image moves — claude_shared tracks no version, and a skill
# installed there would drift inside running containers exactly the way the
# CLI auto-updater above does. The dispatcher delivers them per call, one
# --plugin-dir per skill, from dispatcher/role_skills.py; SKILLS_ROOT there
# is this destination path, and a test asserts the two still agree. The whole
# directory is copied, loose NOTICE.md and README.md included: the three
# upstreams are MIT, which requires their notices to travel with the copies,
# and --plugin-dir ignores files that are not plugins.
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
