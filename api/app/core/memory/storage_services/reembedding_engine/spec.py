from dataclasses import dataclass

from app.core.memory.storage.enums import MemoryNodeType


@dataclass(frozen=True, slots=True)
class ReembedTarget:
    """一类节点的重算目标。"""

    label: MemoryNodeType
    #: 写回的向量属性名（图中不带维度后缀）。
    vector_field: str
    #: 参与向量化的源文本属性名。
    text_field: str


#: 全部"写入链真的会产出向量"的节点类型。文本为空时跳过该节点，与原写入链一致。
REEMBED_TARGETS: tuple[ReembedTarget, ...] = (
    ReembedTarget(MemoryNodeType.STATEMENT, "statement_embedding", "statement"),
    ReembedTarget(MemoryNodeType.CHUNK, "chunk_embedding", "content"),
    ReembedTarget(MemoryNodeType.EXTRACTED_ENTITY, "name_embedding", "name"),
    ReembedTarget(
        MemoryNodeType.MEMORY_SUMMARY,
        "summary_embedding",
        "content",
    ),
    ReembedTarget(MemoryNodeType.SCENE_SUMMARY, "summary_embedding", "content"),
    ReembedTarget(MemoryNodeType.COMMUNITY, "summary_embedding", "summary"),
    ReembedTarget(MemoryNodeType.PERCEPTUAL, "summary_embedding", "summary"),
    ReembedTarget(MemoryNodeType.DIALOGUE, "dialog_embedding", "content"),
    ReembedTarget(MemoryNodeType.USER_SOURCE, "text_embedding", "original_text"),
)

# 不重算的带向量字段：``AssistantPruned.text_embedding``。
# 该字段在 ES 索引与 Neo4j 向量索引里都有定义，但写入链无内容填充
UNREBUILT_VECTOR_FIELDS: tuple[tuple[MemoryNodeType, str], ...] = (
    (MemoryNodeType.ASSISTANT_PRUNED, "text_embedding"),
)

#: label → 重算目标的反查表，供单节点重算（如遗忘恢复）复用同一套映射。
_REEMBED_TARGET_BY_LABEL: dict[MemoryNodeType, ReembedTarget] = {
    target.label: target for target in REEMBED_TARGETS
}


def reembed_target_for(label: MemoryNodeType) -> ReembedTarget | None:
    """Return the re-embed target for a label, or ``None`` when it has no vector.

    ``None`` means the label's write path never produces a vector (or the field
    is deliberately not rebuilt), so there is nothing to recompute.
    """
    return _REEMBED_TARGET_BY_LABEL.get(label)


__all__ = [
    "REEMBED_TARGETS",
    "UNREBUILT_VECTOR_FIELDS",
    "ReembedTarget",
    "reembed_target_for",
]
