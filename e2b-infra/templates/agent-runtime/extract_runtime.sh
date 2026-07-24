#!/bin/bash
# Extract runtime package from api/app into the template build context
#
# This script copies the minimal set of Python modules from the main API
# that are needed for Agent/Workflow execution inside the sandbox.
#
# The sandbox runtime does NOT include:
#   - Database models/repositories (no DB access)
#   - Celery tasks (no background workers)
#   - Controllers/routes (no HTTP endpoints, except callback client)
#   - Document parsing (RAG ingestion happens outside sandbox)
#   - Heavy ML models (torch, onnxruntime, etc.)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
API_SRC="$PROJECT_ROOT/api/app"
RUNTIME_DIR="$SCRIPT_DIR/runtime"

echo "=== Extracting runtime package ==="
echo "Source: $API_SRC"
echo "Target: $RUNTIME_DIR"

# Clean previous extraction
rm -rf "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR"

# ──────────────────────────────────────────────────────────────
# 1. Agent execution engine
# ──────────────────────────────────────────────────────────────
echo "  -> core/agent/"
mkdir -p "$RUNTIME_DIR/core/agent"
cp "$API_SRC/core/agent/langchain_agent.py" "$RUNTIME_DIR/core/agent/"
# Copy agent creation and model utilities
find "$API_SRC/core/agent/" -name "*.py" -not -name "*middleware*" \
    -exec cp {} "$RUNTIME_DIR/core/agent/" \;
touch "$RUNTIME_DIR/core/__init__.py"
touch "$RUNTIME_DIR/core/agent/__init__.py"

# ──────────────────────────────────────────────────────────────
# 2. Model layer (LLM wrappers - RedBearLLM, RedBearModelConfig)
# ──────────────────────────────────────────────────────────────
echo "  -> core/models/"
mkdir -p "$RUNTIME_DIR/core/models"
if [ -d "$API_SRC/core/models" ]; then
    find "$API_SRC/core/models/" -name "*.py" \
        -exec cp {} "$RUNTIME_DIR/core/models/" \;
fi
touch "$RUNTIME_DIR/core/models/__init__.py"

# ──────────────────────────────────────────────────────────────
# 3. Workflow execution engine
# ──────────────────────────────────────────────────────────────
echo "  -> core/workflow/"
mkdir -p "$RUNTIME_DIR/core/workflow/engine"
mkdir -p "$RUNTIME_DIR/core/workflow/nodes"
mkdir -p "$RUNTIME_DIR/core/workflow/variable"

# Engine (executor, graph builder, state manager, variable pool, etc.)
find "$API_SRC/core/workflow/engine/" -name "*.py" \
    -exec cp {} "$RUNTIME_DIR/core/workflow/engine/" \;

# Nodes (all node types)
cp -r "$API_SRC/core/workflow/nodes/"* "$RUNTIME_DIR/core/workflow/nodes/" 2>/dev/null || true
# Remove __pycache__
find "$RUNTIME_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Variable system
if [ -d "$API_SRC/core/workflow/variable" ]; then
    find "$API_SRC/core/workflow/variable/" -name "*.py" \
        -exec cp {} "$RUNTIME_DIR/core/workflow/variable/" \;
fi

touch "$RUNTIME_DIR/core/workflow/__init__.py"
touch "$RUNTIME_DIR/core/workflow/engine/__init__.py"
touch "$RUNTIME_DIR/core/workflow/nodes/__init__.py"
touch "$RUNTIME_DIR/core/workflow/variable/__init__.py"

# ──────────────────────────────────────────────────────────────
# 4. Tool wrappers (LangChain tool adapters)
# ──────────────────────────────────────────────────────────────
echo "  -> services/tool_service.py (partial)"
mkdir -p "$RUNTIME_DIR/services"
touch "$RUNTIME_DIR/services/__init__.py"

# Copy tool-related services (will be patched for sandbox use)
for f in tool_service.py tool_wrapper.py; do
    if [ -f "$API_SRC/services/$f" ]; then
        cp "$API_SRC/services/$f" "$RUNTIME_DIR/services/"
    fi
done

# ──────────────────────────────────────────────────────────────
# 5. Utility modules
# ──────────────────────────────────────────────────────────────
echo "  -> core/utils/"
mkdir -p "$RUNTIME_DIR/core/utils"
if [ -d "$API_SRC/core/utils" ]; then
    find "$API_SRC/core/utils/" -name "*.py" \
        -exec cp {} "$RUNTIME_DIR/core/utils/" \;
fi
touch "$RUNTIME_DIR/core/utils/__init__.py"

# ──────────────────────────────────────────────────────────────
# 6. Configuration (sandbox-specific)
# ──────────────────────────────────────────────────────────────
echo "  -> config (sandbox-specific)"
mkdir -p "$RUNTIME_DIR/config"
touch "$RUNTIME_DIR/config/__init__.py"

# ──────────────────────────────────────────────────────────────
# 7. Entrypoint
# ──────────────────────────────────────────────────────────────
echo "  -> entrypoint.py"
cp "$SCRIPT_DIR/../../runtime-entrypoint/entrypoint.py" "$RUNTIME_DIR/entrypoint.py" 2>/dev/null || true
cp "$SCRIPT_DIR/../../runtime-entrypoint/protocol.py" "$RUNTIME_DIR/protocol.py" 2>/dev/null || true
cp "$SCRIPT_DIR/../../runtime-entrypoint/callback_client.py" "$RUNTIME_DIR/callback_client.py" 2>/dev/null || true

# Root __init__.py
touch "$RUNTIME_DIR/__init__.py"

echo ""
echo "=== Extraction complete ==="
echo "Files extracted to: $RUNTIME_DIR"
echo ""
echo "NOTE: You may need to patch imports in the extracted files."
echo "      The runtime uses 'runtime.*' as the import prefix instead of 'app.*'."
echo ""
echo "Next steps:"
echo "  1. Review extracted files for unnecessary DB/Celery imports"
echo "  2. Run: ./build.sh"
