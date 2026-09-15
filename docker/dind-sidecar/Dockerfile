# docker/dind-sidecar/Dockerfile
# Build context: docker/dind-sidecar (image is built by hand and referenced
# by name from docker/compose/docker-compose.agents.yml).
FROM docker:dind

RUN apk add --no-cache dcron

COPY prune.sh /usr/local/bin/prune.sh
RUN chmod +x /usr/local/bin/prune.sh

COPY crontab /etc/crontabs/root

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
