# docker/agent/Dockerfile
# Build context: repo root (the hook scripts under hooks/ are copied in), e.g.
#   docker build -f docker/agent/Dockerfile -t ia-harness-agent .
FROM docker:27-cli AS docker-cli
FROM node:20-slim

# python3-requests comes from apt rather than pip: this base is Debian
# bookworm, whose system Python is PEP 668 externally-managed, so a plain
# `pip install requests` fails and the alternatives (--break-system-packages
# or a venv) buy nothing here — requests is the hook's only dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        python3 \
        python3-requests \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

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
