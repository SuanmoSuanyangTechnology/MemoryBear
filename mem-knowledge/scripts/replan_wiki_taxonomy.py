"""目录重规划工具：默认预览，--apply 只应用已保存且快照仍匹配的计划。"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from src.rag.wiki.diagnostics import diagnostic_json, exception_details  # noqa: E402
from src.runtime import get_worker_runtime, shutdown_worker_runtime_sync  # noqa: E402
from src.services.wiki.taxonomy import apply_taxonomy, preview_taxonomy  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-db-name", required=True, help="必须与当前生效数据库一致")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--output", type=Path, help="保存预览计划，不写目录")
    operation.add_argument("--apply", type=Path, help="读取并应用已核对的预览计划")
    args = parser.parse_args()
    runtime = get_worker_runtime()
    try:
        if runtime.settings.db_name != args.expected_db_name:
            raise ValueError(f"数据库不匹配：实际 {runtime.settings.db_name}")
        if args.output:
            # 避免覆盖已经核对的计划文件。
            if args.output.exists():
                raise FileExistsError(f"预览计划已存在：{args.output}")
            plan = preview_taxonomy(runtime, args.kb_id)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(plan, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(json.dumps({"mode": "preview", "target": plan["target"],
                              "pages": len(plan["pages"]), "plan": str(args.output)},
                             ensure_ascii=False))
        else:
            plan = json.loads(args.apply.read_text(encoding="utf-8"))
            print(json.dumps(apply_taxonomy(runtime, args.kb_id, plan), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(diagnostic_json({
            "stage": "taxonomy_preview" if args.output else "taxonomy_apply",
            "knowledge_id": str(args.kb_id), **exception_details(exc),
        }), file=sys.stderr)
        return 1
    finally:
        shutdown_worker_runtime_sync()


if __name__ == "__main__":
    raise SystemExit(main())
