#!/usr/bin/env python3
"""
E2B Self-Hosted Infrastructure - End-to-End Test

验证完整的 sandbox 执行流程：
  1. Orchestrator 健康检查
  2. 注册/确认模板存在
  3. 创建 sandbox
  4. 等待 sandbox 就绪
  5. 写入文件到 sandbox
  6. 在 sandbox 中执行命令
  7. 流式执行命令并读取输出
  8. 读取文件
  9. 销毁 sandbox

Usage:
    python test_e2e.py [--orchestrator-url http://localhost:3001] [--api-secret changeme]
"""
import argparse
import asyncio
import json
import sys
import time

import httpx

# ─── Configuration ────────────────────────────────────────────
DEFAULT_ORCHESTRATOR_URL = "http://localhost:3001"
DEFAULT_API_SECRET = "changeme"
DEFAULT_TEMPLATE_NAME = "agent-runtime"


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def ok(msg: str):
    print(f"  {Colors.GREEN}✓{Colors.RESET} {msg}")


def fail(msg: str):
    print(f"  {Colors.RED}✗{Colors.RESET} {msg}")


def info(msg: str):
    print(f"  {Colors.CYAN}→{Colors.RESET} {msg}")


def section(title: str):
    print(f"\n{Colors.BOLD}[{title}]{Colors.RESET}")


class E2ETest:
    def __init__(self, orchestrator_url: str, api_secret: str):
        self.base_url = orchestrator_url.rstrip("/")
        self.headers = {"x-api-key": api_secret}
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=httpx.Timeout(30.0),
        )
        self.sandbox_id = None
        self.errors = []

    async def close(self):
        await self.client.aclose()

    # ──────────────────────────────────────────────────────────
    # Test Steps
    # ──────────────────────────────────────────────────────────

    async def test_health(self) -> bool:
        """Step 1: Health check"""
        section("1. Health Check")
        try:
            resp = await self.client.get("/health")
            if resp.status_code == 200:
                data = resp.json()
                ok(f"Orchestrator healthy: {json.dumps(data, indent=2)}")
                return True
            else:
                fail(f"Health check failed: HTTP {resp.status_code}")
                self.errors.append(f"Health check: HTTP {resp.status_code}")
                return False
        except httpx.ConnectError as e:
            fail(f"Cannot connect to orchestrator at {self.base_url}: {e}")
            self.errors.append(f"Connection failed: {e}")
            return False

    async def test_register_template(self) -> bool:
        """Step 2: Register a simple test template"""
        section("2. Register Template")

        # Check if template already exists
        resp = await self.client.get("/v1/templates")
        if resp.status_code == 200:
            templates = resp.json()
            for t in templates:
                if t.get("name") == DEFAULT_TEMPLATE_NAME:
                    ok(f"Template '{DEFAULT_TEMPLATE_NAME}' already registered (id={t['template_id']})")
                    return True

        # Build a minimal test template
        dockerfile = """FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx
CMD ["sleep", "infinity"]
"""
        info(f"Building template '{DEFAULT_TEMPLATE_NAME}'...")
        resp = await self.client.post(
            "/v1/templates/build",
            json={"name": DEFAULT_TEMPLATE_NAME, "dockerfile": dockerfile},
        )

        if resp.status_code == 200:
            data = resp.json()
            template_id = data.get("template_id")
            ok(f"Template build initiated: id={template_id}, status={data.get('status')}")

            # Wait for build to complete
            info("Waiting for template build to complete...")
            for i in range(60):  # Max 60 seconds
                await asyncio.sleep(2)
                check = await self.client.get(f"/v1/templates/{template_id}")
                if check.status_code == 200:
                    tdata = check.json()
                    if tdata.get("status") == "ready":
                        ok(f"Template built successfully")
                        return True
                    elif tdata.get("status") == "error":
                        fail(f"Template build failed")
                        self.errors.append("Template build error")
                        return False
                    info(f"  Still building... ({i*2}s)")

            fail("Template build timed out")
            self.errors.append("Template build timeout")
            return False
        else:
            fail(f"Template registration failed: HTTP {resp.status_code} - {resp.text}")
            self.errors.append(f"Template register: {resp.text}")
            return False

    async def test_create_sandbox(self) -> bool:
        """Step 3: Create a sandbox"""
        section("3. Create Sandbox")

        resp = await self.client.post(
            "/v1/sandboxes",
            json={
                "template_id": DEFAULT_TEMPLATE_NAME,
                "timeout": 120,
                "env_vars": {"TEST_VAR": "hello_e2b"},
                "metadata": {"test": "e2e"},
                "cpu_count": 1,
                "memory_mb": 256,
                "enable_network": True,
            },
        )

        if resp.status_code == 200:
            data = resp.json()
            self.sandbox_id = data.get("sandbox_id")
            ok(f"Sandbox created: id={self.sandbox_id}, status={data.get('status')}")
            return True
        else:
            fail(f"Create sandbox failed: HTTP {resp.status_code} - {resp.text}")
            self.errors.append(f"Create sandbox: {resp.text}")
            return False

    async def test_wait_ready(self) -> bool:
        """Step 4: Wait for sandbox to be running"""
        section("4. Wait for Sandbox Ready")

        info(f"Waiting for sandbox {self.sandbox_id} to be running...")
        for i in range(30):
            await asyncio.sleep(1)
            resp = await self.client.get(f"/v1/sandboxes/{self.sandbox_id}")
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status")
                if status == "running":
                    ok(f"Sandbox is running (took ~{i+1}s)")
                    return True
                elif status == "error":
                    fail(f"Sandbox failed to start")
                    self.errors.append("Sandbox start error")
                    return False
                info(f"  Status: {status} ({i+1}s)")

        fail("Sandbox did not become ready within 30s")
        self.errors.append("Sandbox ready timeout")
        return False

    async def test_write_file(self) -> bool:
        """Step 5: Write a file into the sandbox"""
        section("5. Write File")

        test_content = json.dumps({"message": "Hello from E2B test!", "timestamp": time.time()})

        resp = await self.client.post(
            f"/v1/sandboxes/{self.sandbox_id}/files",
            json={
                "path": "/app/test_config.json",
                "content": test_content,
                "is_base64": False,
            },
        )

        if resp.status_code == 200:
            ok(f"File written: /app/test_config.json ({len(test_content)} bytes)")
            return True
        else:
            fail(f"Write file failed: HTTP {resp.status_code} - {resp.text}")
            self.errors.append(f"Write file: {resp.text}")
            return False

    async def test_run_command(self) -> bool:
        """Step 6: Run a command in the sandbox"""
        section("6. Run Command")

        resp = await self.client.post(
            f"/v1/sandboxes/{self.sandbox_id}/commands",
            json={
                "cmd": "echo $TEST_VAR && python -c \"import json; data=json.load(open('/app/test_config.json')); print(data['message'])\"",
                "timeout": 10,
                "cwd": "/app",
            },
        )

        if resp.status_code == 200:
            data = resp.json()
            stdout = data.get("stdout", "").strip()
            stderr = data.get("stderr", "").strip()
            exit_code = data.get("exit_code")
            duration = data.get("duration_ms")

            if exit_code == 0:
                ok(f"Command succeeded (exit=0, {duration}ms)")
                ok(f"  stdout: {stdout}")
                if stderr:
                    info(f"  stderr: {stderr}")
                return True
            else:
                fail(f"Command failed (exit={exit_code})")
                fail(f"  stdout: {stdout}")
                fail(f"  stderr: {stderr}")
                self.errors.append(f"Command exit code: {exit_code}")
                return False
        else:
            fail(f"Run command failed: HTTP {resp.status_code} - {resp.text}")
            self.errors.append(f"Run command: {resp.text}")
            return False

    async def test_stream_command(self) -> bool:
        """Step 7: Run a command with streaming output"""
        section("7. Stream Command")

        # Use a command that outputs multiple lines over time
        cmd = "for i in 1 2 3; do echo \"line_$i\"; sleep 0.5; done && echo '{\"event\": \"done\", \"data\": {\"count\": 3}}'"

        try:
            events = []
            async with self.client.stream(
                "POST",
                f"/v1/sandboxes/{self.sandbox_id}/commands/stream",
                json={"cmd": cmd, "timeout": 10, "cwd": "/app"},
            ) as resp:
                if resp.status_code != 200:
                    fail(f"Stream command failed: HTTP {resp.status_code}")
                    self.errors.append(f"Stream: HTTP {resp.status_code}")
                    return False

                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            event = json.loads(data_str)
                            events.append(event)
                        except json.JSONDecodeError:
                            pass

            stdout_events = [e for e in events if e.get("type") == "stdout"]
            exit_event = next((e for e in events if e.get("type") == "exit"), None)

            if stdout_events:
                ok(f"Received {len(stdout_events)} stdout events")
                for e in stdout_events[:5]:
                    info(f"  {e.get('data', '')}")
            if exit_event:
                ok(f"Exit event received: code={exit_event.get('exit_code')}")
            else:
                info("No exit event (may be normal for streaming)")

            return len(stdout_events) > 0

        except Exception as e:
            fail(f"Stream command error: {e}")
            self.errors.append(f"Stream: {e}")
            return False

    async def test_read_file(self) -> bool:
        """Step 8: Read a file from the sandbox"""
        section("8. Read File")

        resp = await self.client.get(
            f"/v1/sandboxes/{self.sandbox_id}/files",
            params={"path": "/app/test_config.json"},
        )

        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", "")
            ok(f"File read back: {content[:100]}")
            # Verify content
            try:
                parsed = json.loads(content)
                if parsed.get("message") == "Hello from E2B test!":
                    ok("File content integrity verified ✓")
                    return True
                else:
                    fail("File content mismatch")
                    self.errors.append("File integrity check failed")
                    return False
            except json.JSONDecodeError:
                fail("File content is not valid JSON")
                self.errors.append("File JSON parse error")
                return False
        else:
            fail(f"Read file failed: HTTP {resp.status_code} - {resp.text}")
            self.errors.append(f"Read file: {resp.text}")
            return False

    async def test_kill_sandbox(self) -> bool:
        """Step 9: Kill the sandbox"""
        section("9. Kill Sandbox")

        resp = await self.client.delete(f"/v1/sandboxes/{self.sandbox_id}")

        if resp.status_code == 200:
            ok(f"Sandbox {self.sandbox_id} killed successfully")
            # Verify it's gone
            await asyncio.sleep(1)
            check = await self.client.get(f"/v1/sandboxes/{self.sandbox_id}")
            if check.status_code == 200:
                status = check.json().get("status")
                if status == "stopped":
                    ok(f"Sandbox status confirmed: stopped")
                else:
                    info(f"Sandbox status: {status}")
            return True
        else:
            fail(f"Kill sandbox failed: HTTP {resp.status_code} - {resp.text}")
            self.errors.append(f"Kill: {resp.text}")
            return False

    # ──────────────────────────────────────────────────────────
    # Run All Tests
    # ──────────────────────────────────────────────────────────

    async def run(self) -> bool:
        """Run all E2E tests in sequence"""
        print(f"\n{Colors.BOLD}{'='*60}")
        print(f"  E2B Self-Hosted Infrastructure - E2E Test")
        print(f"  Orchestrator: {self.base_url}")
        print(f"{'='*60}{Colors.RESET}")

        start = time.time()
        results = []

        # Step 1: Health
        passed = await self.test_health()
        results.append(("Health Check", passed))
        if not passed:
            print(f"\n{Colors.RED}Cannot proceed without healthy orchestrator.{Colors.RESET}")
            return False

        # Step 2: Template
        passed = await self.test_register_template()
        results.append(("Register Template", passed))
        if not passed:
            print(f"\n{Colors.RED}Cannot proceed without template.{Colors.RESET}")
            return False

        # Step 3: Create sandbox
        passed = await self.test_create_sandbox()
        results.append(("Create Sandbox", passed))
        if not passed:
            return False

        # Step 4: Wait ready
        passed = await self.test_wait_ready()
        results.append(("Wait Ready", passed))
        if not passed:
            # Try to cleanup
            if self.sandbox_id:
                await self.client.delete(f"/v1/sandboxes/{self.sandbox_id}")
            return False

        # Step 5: Write file
        passed = await self.test_write_file()
        results.append(("Write File", passed))

        # Step 6: Run command
        passed = await self.test_run_command()
        results.append(("Run Command", passed))

        # Step 7: Stream command
        passed = await self.test_stream_command()
        results.append(("Stream Command", passed))

        # Step 8: Read file
        passed = await self.test_read_file()
        results.append(("Read File", passed))

        # Step 9: Kill sandbox
        passed = await self.test_kill_sandbox()
        results.append(("Kill Sandbox", passed))

        # Summary
        elapsed = time.time() - start
        total = len(results)
        passed_count = sum(1 for _, p in results if p)
        failed_count = total - passed_count

        print(f"\n{Colors.BOLD}{'='*60}")
        print(f"  Results: {passed_count}/{total} passed, {failed_count} failed ({elapsed:.1f}s)")
        print(f"{'='*60}{Colors.RESET}")

        for name, passed in results:
            icon = f"{Colors.GREEN}✓{Colors.RESET}" if passed else f"{Colors.RED}✗{Colors.RESET}"
            print(f"  {icon} {name}")

        if self.errors:
            print(f"\n{Colors.RED}Errors:{Colors.RESET}")
            for e in self.errors:
                print(f"    - {e}")

        all_passed = failed_count == 0
        print(f"\n{'  ' + Colors.GREEN + 'ALL TESTS PASSED ✓' + Colors.RESET if all_passed else '  ' + Colors.RED + 'SOME TESTS FAILED ✗' + Colors.RESET}\n")
        return all_passed


async def main():
    parser = argparse.ArgumentParser(description="E2B E2E Test")
    parser.add_argument(
        "--orchestrator-url",
        default=DEFAULT_ORCHESTRATOR_URL,
        help=f"Orchestrator URL (default: {DEFAULT_ORCHESTRATOR_URL})",
    )
    parser.add_argument(
        "--api-secret",
        default=DEFAULT_API_SECRET,
        help="API secret for authentication",
    )
    args = parser.parse_args()

    test = E2ETest(args.orchestrator_url, args.api_secret)
    try:
        success = await test.run()
        sys.exit(0 if success else 1)
    finally:
        await test.close()


if __name__ == "__main__":
    asyncio.run(main())
