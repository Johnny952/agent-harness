# docker/dind-sidecar/Dockerfile
FROM docker:dind

RUN apk add --no-cache dcron

COPY prune.sh /usr/local/bin/prune.sh
RUN chmod +x /usr/local/bin/prune.sh
COPY crontab /etc/crontabs/root

ENTRYPOINT ["sh", "-c", "crond -b && dockerd-entrypoint.sh"]
