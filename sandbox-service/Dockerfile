# Build context is the `core/` directory (same as the other microservices):
#   docker build -f sandbox-service/Dockerfile -t sandbox-service:latest .
ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE} AS builder

ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG NEED_MIRROR=1
# Backend extras to install (one extra per backend, see pyproject.toml).
ARG SANDBOX_BACKEND_EXTRA=cube

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

RUN if [ "$NEED_MIRROR" = "1" ]; then \
        pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
        pip3 config set global.trusted-host pypi.tuna.tsinghua.edu.cn; \
        mkdir -p /etc/uv && \
        printf '[[index]]\nurl = "https://pypi.tuna.tsinghua.edu.cn/simple"\ndefault = true\n' > /etc/uv/uv.toml; \
        pip3 install --no-cache-dir uv==0.10.11; \
    else \
        pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" uv==0.10.11; \
    fi

COPY sandbox-service/pyproject.toml sandbox-service/uv.lock /code/sandbox-service/

WORKDIR /code/sandbox-service
RUN if [ "$NEED_MIRROR" = "1" ]; then \
        sed -i 's|pypi.org|pypi.tuna.tsinghua.edu.cn|g' uv.lock; \
    fi
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --extra "${SANDBOX_BACKEND_EXTRA}"

COPY sandbox-service/src /code/sandbox-service/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --extra "${SANDBOX_BACKEND_EXTRA}" && \
    rm -f \
      .venv/lib/python3.12/site-packages/_virtualenv.pth \
      .venv/lib/python3.12/site-packages/distutils-precedence.pth

FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORYBEAR_ROOT_DIR=/code \
    SANDBOX_SERVICE_DIR=/code/sandbox-service \
    PATH=/code/sandbox-service/.venv/bin:$PATH

WORKDIR /code/sandbox-service

RUN useradd --create-home --uid 10001 memsandbox

COPY --from=builder --chown=memsandbox:memsandbox \
    /code/sandbox-service/.venv /code/sandbox-service/.venv
COPY --from=builder --chown=memsandbox:memsandbox \
    /code/sandbox-service/src /code/sandbox-service/src

USER memsandbox

# Configuration comes from the environment (SANDBOX_BACKEND, SANDBOX_API_TOKEN,
# SANDBOX_CUBE_API_URL, ...); no .env is baked into the image.
EXPOSE 8080

# /health also probes the Cube backend: it returns 503 while the substrate is unreachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"

CMD ["uvicorn", "--factory", "src.main:create_app_from_env", "--host", "0.0.0.0", "--port", "8080"]
