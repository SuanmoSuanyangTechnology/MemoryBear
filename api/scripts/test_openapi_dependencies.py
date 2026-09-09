import tempfile
from pathlib import Path
import unittest

from check_openapi_dependencies import validate


class DependencyDriftTests(unittest.TestCase):
    def fixture(self, directory, *, lock_version="1.0", declared="==1.0", exported="1.0"):
        root = Path(directory)
        (root / "uv.lock").write_text(f'[[package]]\nname="fastapi"\nversion="{lock_version}"\n')
        (root / "pyproject.toml").write_text(f'[project]\ndependencies=["fastapi{declared}"]\n')
        (root / "requirements-openapi.txt").write_text(f'fastapi=={exported}\n../packages/redbear-model\n')
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
            root = self.fixture(directory)
            (root / "requirements-openapi.txt").write_text('../packages/redbear-model\n')
            with self.assertRaisesRegex(ValueError, "missing export dependency"):
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
