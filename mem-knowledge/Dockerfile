ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG TIKA_VERSION=3.1.0
ARG TIKA_SHA1=d32d863e77480fae2885d24973d04a07082c8f19

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORYBEAR_ROOT_DIR=/code \
    KB_SERVICE_DIR=/code/mem-knowledge \
    TIKA_SERVER_JAR=file:///opt/tika/tika-server-standard-3.1.0.jar \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache \
    PATH=/code/mem-knowledge/.venv/bin:$PATH

WORKDIR /code

RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
      ca-certificates \
      curl \
      default-jre-headless \
      fonts-noto-cjk \
      fonts-wqy-zenhei \
      libreoffice-impress \
      libreoffice-writer && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /opt/tika && \
    curl -fsSL \
      "https://repo1.maven.org/maven2/org/apache/tika/tika-server-standard/${TIKA_VERSION}/tika-server-standard-${TIKA_VERSION}.jar" \
      -o "/opt/tika/tika-server-standard-${TIKA_VERSION}.jar" && \
    echo "${TIKA_SHA1}  /opt/tika/tika-server-standard-${TIKA_VERSION}.jar" | sha1sum -c -

RUN pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" uv==0.10.11
COPY packages/redbear-model /code/packages/redbear-model
COPY packages/storage-core /code/packages/storage-core
COPY mem-knowledge/pyproject.toml mem-knowledge/uv.lock /code/mem-knowledge/

WORKDIR /code/mem-knowledge
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project && \
    mkdir -p "${TIKTOKEN_CACHE_DIR}" && \
    .venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'

COPY mem-knowledge/src /code/mem-knowledge/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev && \
    useradd --create-home --uid 10001 memknowledge && \
    mkdir -p /files && \
    chown -R memknowledge:memknowledge /code /files /opt/tiktoken-cache

USER memknowledge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["CMD-SHELL", "[ \"$KB_PROCESS_ROLE\" != \"api\" ] || python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/internal/v1/health/live', timeout=3).read()\""]

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
