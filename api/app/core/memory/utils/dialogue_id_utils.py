"""
dialogue_id_utils — Dialogue 节点确定性 ID 工具
前缀统一为中性 ``Dialog_``（快慢共用，覆盖后同一节点也用该前缀，不产生语义误导）。
"""

from __future__ import annotations

# Dialogue 节点 ID 前缀（快慢统一，勿随意修改，否则两路无法 MERGE 到同一节点）。
DIALOGUE_ID_PREFIX = "Dialog_"


def build_dialogue_id(conv_id: str, seq: int, source: str, user_id: str) -> str:
    if conv_id:
        return f"{DIALOGUE_ID_PREFIX}{conv_id}_{seq}"
    return f"{DIALOGUE_ID_PREFIX}{user_id}_{source}_{seq}"
