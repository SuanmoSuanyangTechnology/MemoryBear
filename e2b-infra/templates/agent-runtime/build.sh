#!/bin/bash
# Build the agent-runner template image
# Usage: ./build.sh
#
# Run from the PROJECT ROOT (MemoryBear-Enterprise/core).
# The Dockerfile context is the project root so it can COPY both api/ and e2b-infra/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PROJECT_ROOT is 3 levels up from templates/agent-runtime/
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# e2b-infra root for finding sibling dirs
E2B_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

IMAGE_NAME="${E2B_TEMPLATE_ID:-agent-runtime}"
IMAGE_TAG="latest"

echo "=== Building Agent Runner Template ==="
echo "Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "Context: ${PROJECT_ROOT}"

cd "$PROJECT_ROOT"

docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    -f "$SCRIPT_DIR/Dockerfile" \
    .

echo "=== Build complete: ${IMAGE_NAME}:${IMAGE_TAG} ==="
echo "Verify: docker run --rm ${IMAGE_NAME}:${IMAGE_TAG} python -c 'from runtime.entrypoint import main; print(\"OK\")'"
