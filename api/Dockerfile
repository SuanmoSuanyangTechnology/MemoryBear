# MemoryBear api 镜像
# 构建 context = core 仓库根（含 packages/、api/；应用文件以 api/ 前缀 COPY）：
#
#   docker build -f api/Dockerfile -t redbear-mem-open:latest .
#   海外构建加 --build-arg NEED_MIRROR=0

FROM python:3.12-slim
USER root
SHELL ["/bin/bash", "-c"]

ARG NEED_MIRROR=1

WORKDIR /code

# Tokenizer cache retained for shared model and vision adapters.
COPY api/cl100k_base.tiktoken /code/res/9b5ad71b2ce5302211f9c61530b329a4922fc6a4

ENV DEBIAN_FRONTEND=noninteractive

# Runtime libraries for the retained model, file and memory capabilities.
RUN --mount=type=cache,id=mem_apt,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates openjdk-25-jre-headless libmagic1 libjemalloc2 libgomp1 pipx git curl && \
    rm -rf /var/lib/apt/lists/*

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
        pip3 config set global.trusted-host pypi.tuna.tsinghua.edu.cn; \
        mkdir -p /etc/uv && \
        echo "[[index]]" > /etc/uv/uv.toml && \
        echo 'url = "https://pypi.tuna.tsinghua.edu.cn/simple"' >> /etc/uv/uv.toml && \
        echo "default = true" >> /etc/uv/uv.toml; \
    fi; \
    pipx install uv

ENV PYTHONDONTWRITEBYTECODE=1 DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
ENV PATH=/root/.local/bin:$PATH


# 5. install dependencies from uv.lock file
# 本地路径依赖（pyproject [tool.uv.sources]）：uv sync 前必须就位，否则 --locked 解析失败；
# uv.lock 位于 /code，../packages/redbear-model 即 /packages/redbear-model
COPY packages/redbear-model /packages/redbear-model
COPY api/pyproject.toml /code/pyproject.toml
COPY api/uv.lock /code/uv.lock
COPY api/app /code/app
COPY api/alembic.ini /code/alembic.ini
COPY api/migrations /code/migrations

# https://github.com/astral-sh/uv/issues/10462
# uv records index url into uv.lock but doesn't failover among multiple indexes
RUN --mount=type=cache,id=mem_uv,target=/root/.cache/uv,sharing=locked \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|pypi.tuna.tsinghua.edu.cn|g' uv.lock; \
    else \
        sed -i 's|pypi.tuna.tsinghua.edu.cn|pypi.org|g' uv.lock; \
    fi; \
    uv sync --locked --no-dev

ENV PATH=/code/.venv/bin:$PATH

