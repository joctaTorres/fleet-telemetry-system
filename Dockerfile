# Test/runtime image for the telemetry service. Used by docker-compose.test.yml
# as the `api` service to run migrations and the integration suite against a real
# Postgres. Routes are added by later changes; for now it ships the persistence
# layer and tests.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for layer caching. Fall back to a non-frozen sync
# if no lockfile is present yet.
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --no-install-project

COPY . .
RUN uv sync

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["pytest", "-q"]
