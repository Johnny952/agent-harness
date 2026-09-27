# observability/board/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
COPY observability/ observability/
# No `COPY dispatcher/`, unlike the api's image next door. The api copies it
# because it parses task files, account state and the debt index through the
# dispatcher's own readers; this service parses none of them — it reads JSON over
# HTTP with `requests`. Needing `dispatcher/` here would mean something is being
# re-derived that the api already answered.
#
# No `package.json` and no lockfile either: docs/charter.md C-7 rules this board
# Flask and Jinja, `flask` and `requests` are already pyproject dependencies and
# Jinja arrives with Flask, so this phase adds no dependency to pin. That saving
# is the ruling's whole point.
RUN pip install --no-cache-dir .

# No USER directive, like its neighbours. Here that is not load-bearing the way
# it is for the api: this container mounts nothing, so there is no root-owned
# host state for a non-root user to be unable to read.
EXPOSE 8790
ENTRYPOINT ["python", "-m", "observability.board.app"]
