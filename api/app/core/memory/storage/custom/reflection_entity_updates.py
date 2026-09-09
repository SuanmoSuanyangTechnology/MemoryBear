"""Layer2 反思 · 实体更新写链（模式 A：经 storage 抽象层）。

三条写链：
- ``merge_entity_description``：写入描述摘要、历史碎片与事件时间线，清空 description。
- ``rename_entity``：改写实体名称。
- ``update_entity_name_embedding``：更新实体名称向量。

三者都是单实体属性赋值、无拓扑变化，因此走模式 A 的标准 CRUD：
经 ``MemoryStorageService.update_node`` 写 Neo4j，由 ``WriteRouter`` 向 PostgreSQL
Outbox 发布该实体的 ``UPSERT`` 投影事件，投影 worker 再同步到 Elasticsearch。
"""

from __future__ import annotations

import logging

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
)
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.service import MemoryStorageService, get_storage_service

logger = logging.getLogger(__name__)


async def _update_entity_fields(
    entity_id: str,
    data: dict,
    storage_service: MemoryStorageService | None,
    *,
    operation: str,
) -> bool:
    """按业务 id 更新单个存活实体，并校验命中身份。

    过滤条件为 ``id`` + ``delete_at IS NULL``。``ExtractedEntity.id`` 有全局唯一约束
    （``repositories/neo4j/create_indexes.py`` 的 ``entity_id_unique``，由 ``main.py``
    启动流程建立），单 id 最多命中一个节点，因此不需要再按 ``end_user_id`` 收窄。

    Args:
        entity_id: 待更新 ``ExtractedEntity`` 的业务 ``id``。
        data: 待写入的字段字典。
        storage_service: 测试注入点，缺省时使用全局或上下文单例。
        operation: 出现在日志与异常消息里的操作名，便于定位是哪条写链出的问题。

    Returns:
        ``True`` 表示已更新；``False`` 表示未命中任何节点（实体已被软删）。

    Raises:
        RuntimeError: mutation 命中了本不该被它改到的节点身份（多条匹配或 id 不符）。
            ``WriteRouter`` 用命中结果推导 Outbox 事件身份，放行会把脏数据扩散到 ES。
    """
    service = storage_service or get_storage_service()
    try:
        result = await service.update_node(
            MemoryNodeType.EXTRACTED_ENTITY,
            data,
            NodeFilter.all_of(
                FilterCondition(field="id", value=entity_id),
                FilterCondition(
                    field="delete_at",
                    operator=FilterOperator.EXISTS,
                    value=False,
                ),
            ),
        )
    except OutboxEnqueueError as exc:
        # 事件溯源主键是 (label, node_id)，日志带 entity_id 与 event_ids 即可定位
        logger.error(
            f"{operation} Neo4j 已提交但 Outbox 入队失败 "
            f"entity_id={entity_id} "
            f"event_ids={[str(i) for i in exc.event_ids]} reason={exc.reason}",
            exc_info=True,
        )
        return True

    # 业务 id 唯一，命中多个说明数据已脏，放行会让 WriteRouter 为每个命中节点各发一条 UPSERT 扩散脏数据
    if result.affected_count > 1:
        raise RuntimeError(
            f"{operation} matched multiple entities for one business id"
        )
    if result.affected_count == 1 and result.ids != [entity_id]:
        raise RuntimeError(f"{operation} updated an unexpected entity id")
    return result.affected_count == 1


async def merge_entity_description(
    entity_id: str,
    *,
    description_summary: str,
    description_timeline: str,
    event_timeline: str,
    storage_service: MemoryStorageService | None = None,
) -> bool:
    """把实体的 description 碎片替换为合并后的摘要。

    ``description`` 被清空，因为调用方已经把原始碎片并入了
    ``description_timeline``；清空它才能让下一轮反思不再重复合并同一批碎片。

    Args:
        entity_id: 待更新 ``ExtractedEntity`` 的业务 ``id``。
        description_summary: 合并后的摘要，替代原碎片。
        description_timeline: 碎片历史，包含被清空的正文。
        event_timeline: 序列化后的事件时间线。
        storage_service: 测试注入点。

    Returns:
        ``True`` 表示实体已更新；``False`` 表示未命中（实体已被软删）。
    """
    return await _update_entity_fields(
        entity_id,
        {
            "description": "",
            "description_summary": description_summary,
            "description_timeline": description_timeline,
            "event_timeline": event_timeline,
        },
        storage_service,
        operation="描述合并",
    )


async def rename_entity(
    entity_id: str,
    *,
    new_name: str,
    storage_service: MemoryStorageService | None = None,
) -> bool:
    """改写实体名称。

    冲突检查（``REFLECTION_RENAME_CHECK_CONFLICT``）是只读查询，留在调用方，
    本函数只负责写入。空名字由 ``Layer2Inspector._try_rename_entity`` 的两层判空拦掉
    （``should_rename_entity`` 与 ``not suggested_name.strip() -> "skipped:empty"``）。

    Args:
        entity_id: 待更名 ``ExtractedEntity`` 的业务 ``id``。
        new_name: 新名称，调用方已 strip。
        storage_service: 测试注入点。

    Returns:
        ``True`` 表示已更名；``False`` 表示未命中（实体已被软删）。

    Raises:
        RuntimeError: mutation 命中了本不该被它改到的节点身份。
    """
    return await _update_entity_fields(
        entity_id,
        {"name": new_name},
        storage_service,
        operation="Entity rename",
    )


async def update_entity_name_embedding(
    entity_id: str,
    *,
    name_embedding: list[float],
    storage_service: MemoryStorageService | None = None,
) -> bool:
    """写入实体名称向量。

    更名后、去重合并后、未识别实体回填后都会重算 name_embedding，三处共用本函数：
    写入语义相同，发出的都是同一个实体的 UPSERT 事件。更名与向量更新分两次调用，
    避免向量计算超时或失败影响名称本身的落库。

    Args:
        entity_id: 目标 ``ExtractedEntity`` 的业务 ``id``。
        name_embedding: 新名称的向量，三个调用点各自用 ``if <向量>:`` 拦掉空值。
        storage_service: 测试注入点。

    Returns:
        ``True`` 表示已写入；``False`` 表示未命中（实体已被软删）。

    Raises:
        RuntimeError: mutation 命中了本不该被它改到的节点身份。
    """
    return await _update_entity_fields(
        entity_id,
        {"name_embedding": name_embedding},
        storage_service,
        operation="Entity name embedding update",
    )
