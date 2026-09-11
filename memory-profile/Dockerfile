FROM python:3.12-slim

ARG NEED_MIRROR=1

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
        pip3 config set global.trusted-host pypi.tuna.tsinghua.edu.cn; \
    fi

# ============================================================================
# 层 1/3：依赖（只随 packages/auth-sdk 与 pyproject.toml 变化）
# ============================================================================
COPY packages/auth-sdk /app/packages/auth-sdk
COPY memory-profile/pyproject.toml /app/memory-profile/pyproject.toml
WORKDIR /app/memory-profile

# pip 缓存挂载（BuildKit）：跨构建复用已下载的 wheel，故此处**不再**传 --no-cache-dir
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e /app/packages/auth-sdk

# 只装 pyproject 里声明的依赖；跳过 auth-sdk 自己（它是 path 依赖，上一行已按路径装好，
# 走 PyPI 会找不到）。用 tomllib 从 pyproject 取，避免在 Dockerfile 里重复维护依赖清单。
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import tomllib; print(*[d for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'] if not d.startswith('auth-sdk')], sep='\n')" > /tmp/app-deps.txt \
 && pip install -r /tmp/app-deps.txt

# ============================================================================
# 层 2/3：应用代码（改这些只重建本层）
# ============================================================================
COPY memory-profile/alembic.ini /app/memory-profile/alembic.ini
# migrations 单独 COPY：多源 COPY 到以 / 结尾的目标会平铺目录内容，
# 而 alembic.ini 的 script_location = migrations 需要 migrations/ 子目录
COPY memory-profile/migrations /app/memory-profile/migrations
# 翻译文件：I18N_CORE_LOCALES_DIR 默认解析为 <服务根>/locales，不进镜像则 i18n 无文案
COPY memory-profile/locales /app/memory-profile/locales
COPY memory-profile/src /app/memory-profile/src
# 装应用自身：--no-deps 表示只构建 wheel（依赖已在层 1 装好），秒级。
# 若不需要从任意 cwd `import src`（uvicorn 从 WORKDIR 启动即可），删掉本行可再省一步。
RUN pip install --no-cache-dir --no-deps .

# ============================================================================
# 层 3/3：运行时配置（放最后：改 .env 不该让上面的 wheel 重建）
# ============================================================================
#COPY memory-profile/.env /app/memory-profile/src/.env

EXPOSE 8081

# exec 前缀：替换 shell 成为 PID 1，K8s 优雅终止（SIGTERM）直达 uvicorn
CMD exec uvicorn src.main:app --host 0.0.0.0 --port 8081