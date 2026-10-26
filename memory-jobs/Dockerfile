FROM python:3.12-slim AS builder

ARG UV_VERSION=0.10.11
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /build/memory-jobs
# Path dependencies from [tool.uv.sources]; uv resolves them under ../packages.
# Dockerfile.dockerignore keeps the other packages out of the build context.
COPY packages /build/packages
COPY memory-jobs/pyproject.toml memory-jobs/uv.lock memory-jobs/README.md ./
COPY memory-jobs/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
RUN groupadd --gid 10001 jobs && useradd --uid 10001 --gid 10001 --no-create-home jobs
WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv ./.venv
COPY --from=builder --chown=10001:10001 /build/memory-jobs/src ./src
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD ["python", "-m", "src.healthcheck"]
# Queue/pool/concurrency are per-deployment choices; override CMD in the
# orchestrator (see deploy/k8s/worker.yaml).
CMD ["celery", "-A", "src.gevent_entry:celery_app", "worker", "--loglevel=INFO", "--pool=gevent", "--concurrency=32", "--queues=memory_tasks", "--hostname=memory-jobs@%h"]
