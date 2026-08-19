ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PYPI_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORYBEAR_ROOT_DIR=/code \
    MEM_KNOWLEDGE_SERVICE_DIR=/code/mem-knowledge \
    PATH=/code/mem-knowledge/.venv/bin:$PATH

WORKDIR /code

RUN pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" uv==0.10.11
COPY packages/redbear-model /code/packages/redbear-model
COPY packages/storage-core /code/packages/storage-core
COPY mem-knowledge/pyproject.toml mem-knowledge/uv.lock /code/mem-knowledge/

WORKDIR /code/mem-knowledge
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY mem-knowledge/src /code/mem-knowledge/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev && \
    useradd --create-home --uid 10001 memknowledge && \
    mkdir -p /files && \
    chown -R memknowledge:memknowledge /code /files

USER memknowledge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/internal/v1/health/live', timeout=3).read()"]

CMD ["uvicorn", "mem_knowledge.main:app", "--host", "0.0.0.0", "--port", "8080"]
