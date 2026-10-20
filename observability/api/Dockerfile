# observability/api/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
FROM python:3.11-slim

# `git`, for one read: `/api/tasks/<task_id>/diff` runs `git diff` over the
# `:ro` project checkouts, the review screen's diff (ADR NNN). Without it that
# route answers `data: null` and says git is missing; nothing else needs it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY observability/ observability/
# The price of one parser per fact: this service reads task files, account
# state and the debt index through the dispatcher's own readers, so the image
# rebuilds when they change. `dispatcher/docker_exec.py` comes along as an
# import and is never called — this service gets no docker socket.
COPY dispatcher/ dispatcher/
RUN pip install --no-cache-dir .

# No USER directive, like the collector and the board, and here it is
# load-bearing: `dispatcher_state/*.json` are written root-owned mode 600, so a
# non-root user in this image is a change to who owns that directory first.
EXPOSE 8789
ENTRYPOINT ["python", "-m", "observability.api.app"]
