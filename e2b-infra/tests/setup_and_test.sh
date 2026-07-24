#!/bin/bash
# E2B E2E 测试前置设置 + 运行测试
#
# 执行步骤：
#   1. 确认 docker compose 服务已启动
#   2. 创建 sandbox 网络
#   3. 构建测试用的 agent-runtime 模板镜像
#   4. 运行 E2E 测试脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ORCHESTRATOR_URL="${E2B_ORCHESTRATOR_URL:-http://localhost:3001}"
API_SECRET="${E2B_ORCHESTRATOR_SECRET:-changeme}"

echo "======================================"
echo "  E2B E2E Test Setup & Run"
echo "======================================"
echo ""

# ─── Step 1: Check infrastructure is running ───
echo "[1/4] Checking E2B infrastructure..."
if ! curl -sf "${ORCHESTRATOR_URL}/health" > /dev/null 2>&1; then
    echo "  ⚠️  Orchestrator not reachable at ${ORCHESTRATOR_URL}"
    echo "  Starting infrastructure..."
    cd "$INFRA_DIR"
    docker compose up -d
    echo "  Waiting 10s for services to start..."
    sleep 10

    if ! curl -sf "${ORCHESTRATOR_URL}/health" > /dev/null 2>&1; then
        echo "  ❌ Orchestrator still not reachable. Check docker compose logs."
        docker compose logs --tail=20
        exit 1
    fi
fi
echo "  ✓ Orchestrator is healthy"

# ─── Step 2: Create sandbox network ───
echo ""
echo "[2/4] Creating sandbox network..."
docker network create e2b-sandbox-net 2>/dev/null && echo "  ✓ Network e2b-sandbox-net created" || echo "  ✓ Network e2b-sandbox-net already exists"

# ─── Step 3: Build agent-runtime template image ───
echo ""
echo "[3/4] Building agent-runtime template image..."
TEMPLATE_IMAGE="e2b-template-agent-runtime:latest"

# Use a minimal test template (faster to build than the full runtime)
if ! docker image inspect "$TEMPLATE_IMAGE" > /dev/null 2>&1; then
    echo "  Building minimal test template..."
    docker build -t "$TEMPLATE_IMAGE" -f - /dev/null <<'DOCKERFILE'
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx
RUN useradd -m sandbox
CMD ["sleep", "infinity"]
DOCKERFILE
    echo "  ✓ Template image built: $TEMPLATE_IMAGE"
else
    echo "  ✓ Template image already exists: $TEMPLATE_IMAGE"
fi

# Register template with orchestrator
echo "  Registering template with orchestrator..."
REGISTER_RESULT=$(curl -s -X POST "${ORCHESTRATOR_URL}/v1/templates/build" \
    -H "Content-Type: application/json" \
    -H "x-api-key: ${API_SECRET}" \
    -d "{
        \"name\": \"agent-runtime\",
        \"dockerfile\": \"FROM python:3.12-slim\nWORKDIR /app\nRUN pip install --no-cache-dir httpx\nCMD [\\\"sleep\\\", \\\"infinity\\\"]\"
    }" 2>/dev/null || echo '{"status":"error"}')
echo "  → ${REGISTER_RESULT}"

# Wait for build
echo "  Waiting for template build..."
for i in $(seq 1 30); do
    sleep 2
    STATUS=$(curl -s "${ORCHESTRATOR_URL}/v1/templates" \
        -H "x-api-key: ${API_SECRET}" 2>/dev/null \
        | python3 -c "import json,sys; ts=json.load(sys.stdin); print(next((t['status'] for t in ts if t['name']=='agent-runtime'),'unknown'))" 2>/dev/null || echo "waiting")
    if [ "$STATUS" = "ready" ]; then
        echo "  ✓ Template ready"
        break
    elif [ "$STATUS" = "error" ]; then
        echo "  ❌ Template build failed"
        break
    fi
    echo "    ... status=$STATUS (${i}*2s)"
done

# ─── Step 4: Run E2E test ───
echo ""
echo "[4/4] Running E2E test..."
echo ""

# Check if httpx is available
if ! python3 -c "import httpx" 2>/dev/null; then
    echo "  Installing httpx for test..."
    pip3 install httpx --quiet 2>/dev/null || pip install httpx --quiet 2>/dev/null
fi

python3 "$SCRIPT_DIR/test_e2e.py" \
    --orchestrator-url "$ORCHESTRATOR_URL" \
    --api-secret "$API_SECRET"
