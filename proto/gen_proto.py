#!/usr/bin/env python3
"""Generate Python protobuf code for the memorybear.* contract tree."""

from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

from grpc_tools import protoc

PROTO_ROOT = Path(__file__).resolve().parent
OUT_ROOT = PROTO_ROOT.parent / "packages" / "memorybear-grpc" / "src"
PROTO_FILES = (
    "memorybear/common/v1/common.proto",
    "memorybear/application/v1/chat.proto",
    "memorybear/model/v1/invoke.proto",
)
_PACKAGE_DIRS = (
    "memorybear_grpc",
    "memorybear/common/v1",
    "memorybear/application/v1",
    "memorybear/model/v1",
)


def _generate(destination: Path) -> None:
    for relative in _PACKAGE_DIRS:
        (destination / relative).mkdir(parents=True, exist_ok=True)
    arguments = [
        "protoc",
        f"-I{PROTO_ROOT}",
        f"--python_out={destination}",
        f"--grpc_python_out={destination}",
        *(str(PROTO_ROOT / name) for name in PROTO_FILES),
    ]
    code = protoc.main(arguments)
    if code != 0:
        raise SystemExit(f"protoc failed with exit code {code}")


def _check() -> int:
    with tempfile.TemporaryDirectory() as temp:
        staged = Path(temp)
        _generate(staged)
        stale: list[str] = []
        for generated in sorted(staged.rglob("*.py")):
            relative = generated.relative_to(staged)
            committed = OUT_ROOT / relative
            if not committed.exists() or not filecmp.cmp(
                generated, committed, shallow=False
            ):
                stale.append(str(relative))
    if stale:
        print("generated code is stale: " + ", ".join(stale))
        return 1
    print("generated code is up to date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return _check()
    _generate(OUT_ROOT)
    print(f"generated into {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
