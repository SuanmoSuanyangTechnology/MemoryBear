#!/usr/bin/env python
"""静态核对雏形（M7-2）：本服务不得碰宿主域（老单体 core/api）。

两项硬检查（AST + SQL 字面量）：

1. **不依赖宿主包**：`src/` 内出现 `import app` / `from app... import` 即失败——服务只
   允许依赖自身模块、`packages/`（redbear-model / auth-sdk）与三方库
2. **不写非认领表**：源码字符串中的 `INSERT INTO / UPDATE / DELETE FROM` 目标表必须属于
   `src.models.base.OWNED_TABLES`（四表）；对 model_api_keys 等冻结实体或他域表的写操作
   一律命中（这类写属老单体链，服务侧只读）

只读检查，不改文件。退出码 0 = 通过，1 = 命中（逐条打印 文件:行 与原因）。

用法：`.venv/bin/python scripts/check_no_host_writes.py [--root src]`
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models.base import OWNED_TABLES  # noqa: E402

HOST_PACKAGE = "app"
DML_PATTERN = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
# SQL 关键字后的词可能是子句而非表名（如 DELETE FROM 后紧跟 SELECT 的少见写法）
SQL_KEYWORDS = frozenset({"select", "set", "values", "where", "only"})


def _iter_python_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_host_modules(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == HOST_PACKAGE or alias.name.startswith(f"{HOST_PACKAGE}."):
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module and (
            node.module == HOST_PACKAGE or node.module.startswith(f"{HOST_PACKAGE}.")
        ):
            hits.append((node.lineno, f"from {node.module} import ..."))
    return hits


def _dml_targets(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for match in DML_PATTERN.finditer(node.value):
            table = match.group(1)
            if table.lower() in SQL_KEYWORDS:
                continue
            if table not in OWNED_TABLES:
                hits.append((node.lineno, f"{match.group(0).strip()} -> {table}（非认领表）"))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="model-service 宿主域静态核对")
    parser.add_argument("--root", default="src", help="扫描根目录（默认 src）")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[check] 扫描根目录不存在: {root}", file=sys.stderr)
        return 2

    violations: list[str] = []
    scanned = 0
    for path in _iter_python_files(root):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, reason in _imported_host_modules(tree) + _dml_targets(tree):
            violations.append(f"{path.relative_to(root.parent)}:{lineno}: {reason}")

    if violations:
        print(f"[check] 命中 {len(violations)} 条宿主域写入/依赖（扫描 {scanned} 文件）：")
        for item in violations:
            print(f"  - {item}")
        return 1

    print(f"[check] OK：{scanned} 文件无宿主包依赖、无非认领表 DML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
