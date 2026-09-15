# observability/dashboard/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
COPY observability/ observability/
RUN pip install --no-cache-dir .

EXPOSE 8788
ENTRYPOINT ["python", "-m", "observability.dashboard.app"]
