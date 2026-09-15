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

RUN npm install -g @anthropic-ai/claude-code

# Observability hook: emit_event.py POSTs each Claude Code hook event to the
# collector (COLLECTOR_URL / SOURCE_APP are set per agent service in
# docker/compose/docker-compose.agents.yml); install_settings.py registers it
# in ~/.claude/settings.json at container start (see entrypoint.sh).
COPY hooks/emit_event.py /usr/local/lib/ia-harness/emit_event.py
COPY hooks/install_settings.py /usr/local/lib/ia-harness/install_settings.py

COPY docker/agent/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /data/projects
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
