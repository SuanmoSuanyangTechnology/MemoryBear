#!/bin/bash
# Build the agent-runtime template image
# Usage: ./build.sh [--push]
#
# Prerequisites:
#   1. Docker running
#   2. runtime/ directory must exist (see extract_runtime.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

IMAGE_NAME="e2b-template-agent-runtime"
IMAGE_TAG="latest"

echo "=== Building E2B Agent Runtime Template ==="
echo "Image: ${IMAGE_NAME}:${IMAGE_TAG}"

# Check if runtime directory exists
if [ ! -d "$SCRIPT_DIR/runtime" ]; then
    echo "Error: runtime/ directory not found."
    echo "Run extract_runtime.sh first to extract the runtime package."
    exit 1
fi

# Build the image
docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    -f "$SCRIPT_DIR/Dockerfile" \
    "$SCRIPT_DIR"

echo "=== Build complete: ${IMAGE_NAME}:${IMAGE_TAG} ==="

# Create docker network if not exists
docker network create e2b-sandbox-net 2>/dev/null || true

# Register template with orchestrator (if running)
if curl -s http://localhost:3001/health > /dev/null 2>&1; then
    echo "=== Registering template with orchestrator ==="
    curl -s -X POST http://localhost:3001/v1/templates/build \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${E2B_ORCHESTRATOR_SECRET:-changeme}" \
        -d "{
            \"name\": \"agent-runtime\",
            \"dockerfile\": \"$(base64 < "$SCRIPT_DIR/Dockerfile")\"
        }" | python3 -m json.tool
    echo ""
else
    echo "Orchestrator not running, skipping template registration."
    echo "You can register manually after starting the infrastructure."
fi
