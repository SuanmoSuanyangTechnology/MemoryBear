FROM python:3.12-slim AS builder

ARG UV_VERSION=0.10.11
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /build/memory-profile
COPY packages/auth-sdk /build/packages/auth-sdk
COPY memory-profile/pyproject.toml memory-profile/uv.lock memory-profile/README.md ./
COPY memory-profile/alembic.ini ./alembic.ini
COPY memory-profile/migrations ./migrations
COPY memory-profile/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    PROFILE_HOST=0.0.0.0 \
    PROFILE_PORT=8082
RUN groupadd --gid 10001 profile && useradd --uid 10001 --gid 10001 --no-create-home profile
WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv ./.venv
COPY --from=builder --chown=10001:10001 /build/memory-profile/alembic.ini ./alembic.ini
COPY --from=builder --chown=10001:10001 /build/memory-profile/migrations ./migrations
COPY --from=builder --chown=10001:10001 /build/memory-profile/src ./src
USER 10001:10001
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/internal/v1/health/live', timeout=2)"]
CMD ["uvicorn", "src.entry:app", "--host", "0.0.0.0", "--port", "8082", "--http", "zttp", "--http2", "--no-access-log"]
