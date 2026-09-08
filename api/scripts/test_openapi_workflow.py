"""Exercise the actual gate shell with deterministic fake diff processes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


class GateTests(unittest.TestCase):
    def run_gate(self, *, approved=False, oas_exit=0, oas_json="[]", docker_exit=0, state="compatible"):
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/api-breaking-change.yml"
        steps = yaml.safe_load(workflow.read_text())["jobs"]["openapi-diff"]["steps"]
        script = next(step["run"] for step in steps if step.get("name") == "Run OpenAPI breaking change checks")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api").mkdir()
            (root / "bin").mkdir()
            (root / "api/openapi-pr-base.json").write_text("{}")
            commands = {
                "oasdiff": '#!/bin/sh\nif [ "$1" = "breaking" ]; then printf "%s\\n" "$OAS_JSON"; exit "$OAS_EXIT"; fi\nexit 0\n',
                "docker": '#!/bin/sh\nprintf "%s\\n" "$DIFF_STATE"\nexit "$DIFF_EXIT"\n',
                "git": '#!/bin/sh\necho 12345678\n',
            }
            for name, content in commands.items():
                path = root / "bin" / name
                path.write_text(content)
                path.chmod(0o755)
            env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}",
                       API_BREAKING_APPROVED=str(approved).lower(), PR_BASE_SHA="12345678",
                       PR_BASE_EXPORTED="true", GITHUB_OUTPUT=str(root / "outputs"),
                       GITHUB_STEP_SUMMARY=str(root / "summary"), OAS_JSON=oas_json,
                       OAS_EXIT=str(oas_exit), DIFF_STATE=state, DIFF_EXIT=str(docker_exit))
            return subprocess.run(
                ["bash", "-e", "-c", script.replace("${{ github.workspace }}", str(root))],
                cwd=root, env=env, capture_output=True, text=True, timeout=10,
            )

    def test_compatible_passes(self):
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_incompatible_rejected_without_approval(self):
        result = self.run_gate(oas_json='[{"level":3}]', state="incompatible")
        self.assertNotEqual(result.returncode, 0)

    def test_valid_incompatibility_can_be_approved(self):
        result = self.run_gate(approved=True, oas_json='[{"level":3}]', state="incompatible")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_docker_error_never_approved(self):
        result = self.run_gate(approved=True, docker_exit=125, state="incompatible")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution failed", result.stdout)

    def test_oasdiff_error_never_approved(self):
        result = self.run_gate(approved=True, oas_exit=1, oas_json='[{"level":3}]')
        self.assertNotEqual(result.returncode, 0)

    def test_malformed_json_rejected(self):
        self.assertNotEqual(self.run_gate(approved=True, oas_json="oops").returncode, 0)

    def test_unknown_diff_state_rejected(self):
        self.assertNotEqual(self.run_gate(approved=True, state="oops").returncode, 0)


if __name__ == "__main__":
    unittest.main()
