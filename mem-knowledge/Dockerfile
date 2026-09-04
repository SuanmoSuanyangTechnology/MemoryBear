ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE} AS builder

ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG TIKA_VERSION=3.1.0
ARG TIKA_SHA1=d32d863e77480fae2885d24973d04a07082c8f19
ARG NEED_MIRROR=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /code

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
        pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
        pip3 config set global.trusted-host pypi.tuna.tsinghua.edu.cn; \
        mkdir -p /etc/uv && \
        printf '[[index]]\nurl = "https://pypi.tuna.tsinghua.edu.cn/simple"\ndefault = true\n' > /etc/uv/uv.toml; \
    fi; \
    apt-get update && \
    apt-get install --yes --no-install-recommends \
      ca-certificates \
      curl && \
    rm -rf /var/lib/apt/lists/*

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        pip3 install --no-cache-dir uv==0.10.11; \
    else \
        pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" uv==0.10.11; \
    fi
COPY packages/redbear-model /code/packages/redbear-model
COPY packages/storage-core /code/packages/storage-core
COPY packages/auth-sdk /code/packages/auth-sdk
COPY mem-knowledge/pyproject.toml mem-knowledge/uv.lock /code/mem-knowledge/

WORKDIR /code/mem-knowledge
RUN if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|pypi.tuna.tsinghua.edu.cn|g' uv.lock; \
    fi
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable

COPY mem-knowledge/src /code/mem-knowledge/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable && \
    rm -f \
      .venv/lib/python3.12/site-packages/_virtualenv.pth \
      .venv/lib/python3.12/site-packages/distutils-precedence.pth && \
    mkdir -p "${TIKTOKEN_CACHE_DIR}" /opt/tika && \
    .venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")' && \
    curl -fsSL \
      "https://repo1.maven.org/maven2/org/apache/tika/tika-server-standard/${TIKA_VERSION}/tika-server-standard-${TIKA_VERSION}.jar" \
      -o "/opt/tika/tika-server-standard-${TIKA_VERSION}.jar" && \
    echo "${TIKA_SHA1}  /opt/tika/tika-server-standard-${TIKA_VERSION}.jar" | sha1sum -c -

FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG NEED_MIRROR=1

ENV KB_PROCESS_ROLE=api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORYBEAR_ROOT_DIR=/code \
    KB_SERVICE_DIR=/code/mem-knowledge \
    TIKA_SERVER_JAR=file:///opt/tika/tika-server-standard-3.1.0.jar \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache \
    PATH=/code/mem-knowledge/.venv/bin:$PATH

WORKDIR /code/mem-knowledge

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi; \
    apt-get update && \
    apt-get install --yes --no-install-recommends \
      ca-certificates \
      curl \
      default-jre-headless \
      fonts-noto-cjk \
      fonts-wqy-zenhei \
      libreoffice-impress \
      libreoffice-writer && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --uid 10001 memknowledge && \
    mkdir -p /files

COPY --from=builder --chown=memknowledge:memknowledge \
    /code/mem-knowledge/.venv /code/mem-knowledge/.venv
COPY --from=builder --chown=memknowledge:memknowledge \
    /code/mem-knowledge/src /code/mem-knowledge/src
COPY --from=builder --chown=memknowledge:memknowledge /opt/tika /opt/tika
COPY --from=builder --chown=memknowledge:memknowledge \
    /opt/tiktoken-cache /opt/tiktoken-cache

RUN chown memknowledge:memknowledge /files

USER memknowledge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD test "${KB_PROCESS_ROLE:-api}" != "api" || python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/internal/v1/health/live', timeout=3).read()"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
