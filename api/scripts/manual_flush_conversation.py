"""应急时手动派发 flush 任务的工具脚本。

使用场景：
- 故障期间，某条 conversation 长期没有被 ScanIdle 兜底（比如 scan_idle 被禁用、Beat 挂掉）
- 需要单独触发某条 conversation 的 flush 处理
- 调试 / 验证 flush 流程

使用方式::

    # 在 api/ 目录下运行
    python scripts/manual_flush_conversation.py <conversation_id>

    # 也可以一次派发多个
    python scripts/manual_flush_conversation.py <conv_id_1> <conv_id_2> <conv_id_3>

幂等保护：内部通过 ``dispatch_flush_if_not_running`` 派发，同一 conv 已有
flush 任务在跑时会被自动跳过。可以反复执行，安全。

测试脚本，暂时不要上传分支
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: python scripts/manual_flush_conversation.py "
            "<conversation_id> [<conversation_id> ...]"
        )
        return 1

    from app.core.memory.sliding_window.flush_dispatcher import (
        dispatch_flush_if_not_running,
    )

    conversation_ids = sys.argv[1:]
    dispatched = 0
    skipped = 0

    for conv_id in conversation_ids:
        success = dispatch_flush_if_not_running(
            conversation_id=conv_id,
            source="manual",
        )
        if success:
            print(f"[OK]   已派发 flush 任务: conv={conv_id}")
            dispatched += 1
        else:
            print(f"[SKIP] 已有 flush 任务在跑或派发失败: conv={conv_id}")
            skipped += 1

    print(f"\n汇总: dispatched={dispatched}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
