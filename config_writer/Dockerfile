# config_writer/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
# The one validator: the writer checks every save with `load_config`, so the
# image rebuilds when the dispatcher's config changes.
COPY dispatcher/ dispatcher/
# The token check only. The rest of `observability/` is not this service's.
COPY observability/__init__.py observability/__init__.py
COPY observability/auth.py observability/auth.py
COPY config_writer/ config_writer/
RUN pip install --no-cache-dir .

# No USER directive, like the api and the collector, and here it is
# load-bearing: docker creates a missing `config.d` bind-mount source
# root-owned, and a non-root user could not then write it (`docs/decisions.md`
# ADR 110 part 8). The container holds no socket and one writable mount.
EXPOSE 8791
ENTRYPOINT ["python", "-m", "config_writer.app"]
