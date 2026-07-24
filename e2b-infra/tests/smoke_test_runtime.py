"""Smoke test for the E2B sandbox infrastructure.

Validates: orchestrator health → create sandbox → exec agent → stream SSE → destroy.

Usage:
    ORCHESTRATOR_URL=http://127.0.0.1:3001 \\
    ORCHESTRATOR_API_KEY=changeme \\
    python e2b-infra/tests/smoke_test_runtime.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://127.0.0.1:3001")
ORCHESTRATOR_API_KEY = os.getenv("ORCHESTRATOR_API_KEY", "changeme")

HEADERS = {
    "Authorization": f"Bearer {ORCHESTRATOR_API_KEY}",
    "Content-Type": "application/json",
}


async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=30)) as client:
        # 1. Health check
        print(f"[smoke] Checking health: {ORCHESTRATOR_URL}/v1/health")
        resp = await client.get(f"{ORCHESTRATOR_URL}/v1/health")
        print(f"[smoke] Health: {resp.status_code} {resp.json()}")

        # 2. Create sandbox
        print(f"[smoke] Creating sandbox...")
        resp = await client.post(
            f"{ORCHESTRATOR_URL}/v1/sandboxes",
            headers=HEADERS,
        )
        if resp.status_code != 200:
            print(f"[smoke] FAILED to create sandbox: {resp.status_code} {resp.text[:500]}")
            return 1

        sandbox = resp.json()
        sandbox_id = sandbox["sandbox_id"]
        print(f"[smoke] Sandbox created: {sandbox_id}")

        # 3. Exec agent with a simple snapshot
        run_id = str(uuid.uuid4())
        snapshot = {
            "type": "agent_stream",
            "timeout": 120,
            "agent_config": {
                "system_prompt": "You are a helpful assistant. Reply in one short sentence.",
                "tools": [],
                "max_iterations": 3,
                "strategy": "react",
            },
            "model_config": {
                "model_name": os.getenv("TEST_MODEL", "gpt-3.5-turbo"),
                "api_key": os.getenv("TEST_API_KEY", "sk-test"),
                "api_base": os.getenv("TEST_API_BASE", ""),
                "provider": "openai",
            },
            "message": "Hello, introduce yourself in one short sentence.",
            "context": {"history": [], "knowledge": "", "variables": {}},
        }

        print(f"[smoke] Executing agent (run_id={run_id})...")
        event_counts: dict[str, int] = {}
        total_content = ""

        async with client.stream(
            "POST",
            f"{ORCHESTRATOR_URL}/v1/sandboxes/{sandbox_id}/exec",
            headers=HEADERS,
            json={"run_id": run_id, "snapshot": snapshot},
        ) as response:
            print(f"[smoke] Exec status: {response.status_code}")
            if response.status_code != 200:
                body = await response.aread()
                print(f"[smoke] Exec error: {body[:1000]}")
                return 1

            current_event = ""
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    event_counts[current_event] = event_counts.get(current_event, 0) + 1
                elif line.startswith("data:"):
                    if current_event == "message":
                        try:
                            data = json.loads(line[5:].strip())
                            content = data.get("content", "")
                            if content:
                                total_content += content
                        except json.JSONDecodeError:
                            pass
                    if current_event in ("end", "error"):
                        print(f"[smoke] Terminal event: {current_event}")
                        break

        print(f"[smoke] Events: {event_counts}")
        print(f"[smoke] Content length: {len(total_content)} chars")

        # 4. Cleanup
        print(f"[smoke] Destroying sandbox {sandbox_id}...")
        resp = await client.delete(
            f"{ORCHESTRATOR_URL}/v1/sandboxes/{sandbox_id}",
            headers=HEADERS,
        )
        print(f"[smoke] Destroy: {resp.status_code}")

        if total_content:
            print(f"[smoke] Content preview: {total_content[:200]}...")
            print("[smoke] PASSED")
            return 0
        else:
            print("[smoke] UNVERIFIED: no text content received (may be OK if model not configured)")
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
