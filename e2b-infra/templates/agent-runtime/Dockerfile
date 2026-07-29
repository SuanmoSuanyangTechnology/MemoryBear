# MemoryBear Agent/Workflow Runtime Template
#
# 包含完整 api/app 代码 + 精简依赖 + DB stubs
# 工作流节点在 sandbox 内执行，DB 操作通过 callback 回到主 API
#
# 构建方式（从项目根目录）:
#   docker build -t e2b-template-agent-runtime:latest \
#     -f e2b-infra/templates/agent-runtime/Dockerfile .

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install runtime deps
COPY e2b-infra/templates/agent-runtime/requirements-runtime.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy the full api/app source (workflow nodes need it)
COPY api/app /app/app

# Copy sandbox runtime entrypoint
COPY e2b-infra/templates/agent-runtime/runtime /app/runtime

# Copy DB/service stubs
COPY e2b-infra/templates/agent-runtime/stubs /app/stubs

# Create dirs
RUN mkdir -p /app/workspace /app/output /tmp/sandbox

# PYTHONPATH: /app allows `import app.xxx` and `import runtime.xxx`
ENV PYTHONPATH=/app

CMD ["sleep", "infinity"]
