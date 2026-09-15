# docker/agent/Dockerfile
FROM docker:27-cli AS docker-cli
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /data/projects
CMD ["sleep", "infinity"]
