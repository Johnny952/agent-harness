# docker/dispatcher/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
FROM docker:27-cli AS docker-cli
FROM python:3.11-slim

# openssh-client for ssh-keygen: `project-keygen` makes a project's deploy key
# here, on the dispatcher's side, and never inside an agent (charter C-5).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY pyproject.toml .
COPY dispatcher/ dispatcher/
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "dispatcher.cli"]
