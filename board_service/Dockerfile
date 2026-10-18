# board_service/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
# The service reads the board through `dispatcher/`, which imports nothing
# outside itself, so the package and the service are all it copies.
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
COPY dispatcher/ dispatcher/
COPY board_service/ board_service/
RUN pip install --no-cache-dir .
EXPOSE 8792
ENTRYPOINT ["python", "-m", "board_service.app"]
