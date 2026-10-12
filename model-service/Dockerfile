ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE} AS builder

ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG NEED_MIRROR=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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
COPY packages/auth-sdk /code/packages/auth-sdk
COPY model-service/pyproject.toml model-service/uv.lock /code/model-service/

WORKDIR /code/model-service
RUN if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|pypi.tuna.tsinghua.edu.cn|g' uv.lock; \
    fi
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable

COPY model-service/src /code/model-service/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable && \
    rm -f \
      .venv/lib/python3.12/site-packages/_virtualenv.pth \
      .venv/lib/python3.12/site-packages/distutils-precedence.pth

FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG NEED_MIRROR=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORYBEAR_ROOT_DIR=/code \
    MODEL_SERVICE_DIR=/code/model-service \
    PATH=/code/model-service/.venv/bin:$PATH

WORKDIR /code/model-service

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi; \
    apt-get update && \
    apt-get install --yes --no-install-recommends \
      ca-certificates \
      curl && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --uid 10001 memmodel

COPY --from=builder --chown=memmodel:memmodel \
    /code/model-service/.venv /code/model-service/.venv
COPY --from=builder --chown=memmodel:memmodel \
    /code/model-service/src /code/model-service/src

USER memmodel

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/internal/v1/health/live', timeout=3).read()"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
