"""显式重跑指定知识库的单个未完成页面（包括硬删除后的清理）。"""

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.runtime import get_worker_runtime
from src.services.wiki.delivery import rerun_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-id", type=UUID, required=True)
    parser.add_argument("--page-id", type=UUID, required=True)
    args = parser.parse_args()
    runtime = get_worker_runtime()
    try:
        if not runtime.settings.wiki_batch_enabled(args.kb_id):
            parser.error("目标知识库尚未启用批量 Wiki")
        scheduled = rerun_page(runtime, str(args.kb_id), str(args.page_id))
        print("已投递" if scheduled else "没有待处理页面")
    finally:
        runtime.close_sync()


if __name__ == "__main__":
    main()
