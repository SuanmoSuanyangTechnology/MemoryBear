import json
import tempfile
from pathlib import Path
import unittest

from check_openapi_dependencies import validate

LOCAL_SOURCES = (
    '[tool.uv.sources]\n'
    'redbear-model = { path = "../packages/redbear-model" }\n'
    'task-bridge-sdk = { path = "../packages/task-bridge-sdk" }\n'
)


class DependencyDriftTests(unittest.TestCase):
    def fixture(self, directory, *, lock_version="1.0", declared="==1.0", exported="1.0",
                dependencies=("fastapi",),
                local_lines=("../packages/redbear-model", "../packages/task-bridge-sdk"),
                requirements_text=None):
        root = Path(directory)
        (root / "uv.lock").write_text(f'[[package]]\nname="fastapi"\nversion="{lock_version}"\n')
        requirements = []
        for name in dependencies:
            requirements.append(f"{name}{declared}" if name == "fastapi" else name)
        (root / "pyproject.toml").write_text(
            f"[project]\ndependencies={json.dumps(requirements)}\n{LOCAL_SOURCES}"
        )
        if requirements_text is None:
            requirements_text = f"fastapi=={exported}\n" + "".join(f"{line}\n" for line in local_lines)
        (root / "requirements-openapi.txt").write_text(requirements_text)
        return root

    def test_matching_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(validate(self.fixture(directory)), 1)

    def test_lock_only_change_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "differs from uv.lock"):
                validate(self.fixture(directory, lock_version="2.0"))

    def test_manifest_only_change_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "violates pyproject.toml"):
                validate(self.fixture(directory, declared="==2.0"))

    def test_missing_dependency_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory, requirements_text="../packages/redbear-model\n")
            with self.assertRaisesRegex(ValueError, "missing export dependency"):
                validate(root)

    def test_local_workspace_dependency_requires_path_entry(self):
        """A new [tool.uv.sources] dependency is satisfied only by its path line."""
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(
                directory,
                dependencies=("fastapi", "task-bridge-sdk"),
                local_lines=("../packages/redbear-model",),
            )
            with self.assertRaisesRegex(ValueError, "task-bridge-sdk: missing export dependency"):
                validate(root)
            (root / "requirements-openapi.txt").write_text(
                "fastapi==1.0\n../packages/redbear-model\n../packages/task-bridge-sdk\n"
            )
            self.assertEqual(validate(root), 1)

    def test_local_source_missing_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory, local_lines=())
            with self.assertRaisesRegex(ValueError, r"local source \.\./packages/redbear-model missing"):
                validate(root)

    def test_undeclared_local_path_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(
                directory,
                requirements_text="fastapi==1.0\n../packages/redbear-model\n../packages/unknown\n",
            )
            with self.assertRaisesRegex(ValueError, "not declared in"):
                validate(root)

    def test_heavy_package_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            with (root / "requirements-openapi.txt").open("a") as stream:
                stream.write("torch==2.2.2\n")
            with self.assertRaisesRegex(ValueError, "heavy package forbidden"):
                validate(root)


if __name__ == "__main__":
    unittest.main()
