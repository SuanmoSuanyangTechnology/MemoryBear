# MemoryBear gateway-service 镜像
# 构建 context = 外层仓库根（含 core/ 子模块）：
#
#   docker build -f core/gateway-service/Dockerfile \
#     -t <registry>/memorybear/gateway-service:latest .
#   海外构建加 --build-arg NEED_MIRROR=0

FROM python:3.12-slim

ARG NEED_MIRROR=1

# 零 apt：asyncpg/psycopg2-binary/pydantic-core 等均有 cp312 manylinux wheel，
# psycopg2-binary 静态捆绑 libpq，无需编译期系统依赖
# pip 走清华镜像（NEED_MIRROR=1 时）
RUN if [ "$NEED_MIRROR" == "1" ]; then \
        pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
        pip3 config set global.trusted-host pypi.tuna.tsinghua.edu.cn; \
    fi

# auth-sdk 先于目标服务安装：gateway pyproject 的 [tool.uv.sources] 仅 uv 识别，
# pip 构建需 auth-sdk 已满足依赖（auth-sdk 不在 PyPI，path 安装为唯一来源）
COPY core/packages/auth-sdk /app/packages/auth-sdk
COPY core/gateway-service/pyproject.toml /app/gateway-service/
COPY core/gateway-service/src /app/gateway-service/src
WORKDIR /app/gateway-service
RUN pip install --no-cache-dir -e /app/packages/auth-sdk && pip install --no-cache-dir .

EXPOSE 8002

# exec 前缀：替换 shell 成为 PID 1，K8s 优雅终止（SIGTERM）直达 uvicorn
CMD exec uvicorn src.main:app --host 0.0.0.0 --port 8002
