# docker/dispatcher/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
FROM docker:27-cli AS docker-cli
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY pyproject.toml .
COPY dispatcher/ dispatcher/
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "dispatcher.cli"]
