"""
PruningPipeline — 单条 assistant 消息语义剪枝流水线

职责：
- 对单条 assistant 消息执行语义剪枝，输出剪枝后内容（A'）
- 先查询 Redis 缓存，命中则直接返回，避免重复 LLM 调用
- 未命中则调用 SemanticPruner 执行剪枝，结果写入 Neo4j 和 Redis 缓存
- 作为 WritePipeline 的预处理步骤被调用，其结果可跨任务缓存复用

Redis key 格式：pruning:{conversation_id}:{message_seq}
TTL：86400 秒（24 小时）

Requirements: 2.1, 2.2, 6.1, 6.2, 6.4
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from app.core.utils.datetime_utils import to_iso_z

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

logger = logging.getLogger(__name__)

# 当 LLM 未返回 memory_type 枚举时使用的占位值（写入 Redis JSON 时用）
_MEMORY_TYPE_NULL = "NULL"


class PruningPipeline:
    """单条 assistant 消息语义剪枝流水线。

    对单条 assistant 消息执行语义剪枝，输出 A'，写入 Neo4j 并缓存到 Redis。

    流程：
    1. 查 Redis 缓存 pruning:{conversation_id}:{message_seq}
    2. 命中 → 直接返回 A'
    3. 未命中 → 调用 LLM 剪枝 → 写 Neo4j → 写 Redis → 返回 A'
    """

    CACHE_TTL = 86400  # 24 小时兜底

    def __init__(
        self,
        memory_config: "MemoryConfig",
        end_user_id: str,
        language: str = "zh",
    ):
        """
        Args:
            memory_config: 不可变的记忆配置对象（从数据库加载）
            end_user_id: 终端用户 ID
            language: 语言 ("zh" | "en")
        """
        self.memory_config = memory_config
        self.end_user_id = end_user_id
        self.language = language

        # 延迟初始化的客户端
        self._llm_client = None
        self._neo4j_connector = None

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    async def prune(
        self,
        conversation_id: str,
        message_seq: int,
        content: str,
        user_content: str = "",
        _pruning_records: Optional[list] = None,
    ) -> tuple[str, bool, Optional[str]]:
        """对单条 assistant 消息执行语义剪枝，同时判断 user 消息是否需要规整。

        1. 查 Redis 缓存 pruning:{conversation_id}:{message_seq}
        2. 命中 → 直接返回 A'（source="cache"）
        3. 未命中 → 调用 LLM 剪枝 → 写 Neo4j → 写 Redis → 返回 A'（source="llm"）

        Args:
            conversation_id: 对话 ID
            message_seq: 消息在对话中的顺序编号
            content: assistant 消息原始内容
            user_content: 紧邻此 assistant 消息之前的 user 消息内容（用于快照 input.msgs）
            _pruning_records: 可选的列表，若传入则将本次剪枝记录追加到其中，
                              供调用方汇总后写入快照

        Returns:
            (pruned_content, should_process_user_msg, processed_user_msg) 元组：
            - pruned_content: 剪枝后的内容（A'）；若无记忆价值则返回空字符串
            - should_process_user_msg: 是否需要对 user 消息做规整
            - processed_user_msg: 规整后的 user 消息文本，不需要处理时为 None
        """
        cache_key = self._cache_key(conversation_id, message_seq)

        # Step 1: 查询 Redis 缓存
        cached_hint, cached_type = await self._get_from_cache(cache_key)
        if cached_hint is not None:
            logger.info(
                f"[PruningPipeline] 缓存命中: key={cache_key}, "
                f"content_len={len(cached_hint)}"
            )
            if _pruning_records is not None:
                _pruning_records.append({
                    "conversation_id": conversation_id,
                    "message_seq": message_seq,
                    "source": "cache",
                    "input": {
                        "msgs": [
                            {"role": "User", "msg": user_content},
                            {"role": "Assistant", "msg": content},
                        ]
                    },
                    "output": {
                        "should_process_user_msg": False,
                        "processed_user_msg": None,
                        "assistant_memory_hint": cached_hint,
                        "assistant_memory_type": cached_type or "NULL",
                    },
                })
            # 缓存命中时无法获取 user 侧剪枝结果，保留原始 user 消息
            return cached_hint, False, None

        # Step 2: 缓存未命中，调用 LLM 剪枝
        logger.info(
            f"[PruningPipeline] 缓存未命中，执行剪枝: "
            f"conv={conversation_id}, seq={message_seq}"
        )
        pruned_content, memory_type, should_process_user_msg, processed_user_msg = await self._call_llm_prune(
            content=content,
            user_content=user_content,
        )

        # Step 3: 写入 Neo4j（Assistant Pruned 节点）
        try:
            await self._write_to_neo4j(conversation_id, message_seq, content, pruned_content)
        except Exception as e:
            logger.warning(
                f"[PruningPipeline] Neo4j 写入失败（不影响主流程）: "
                f"conv={conversation_id}, seq={message_seq}, err={e}",
                exc_info=True,
            )

        # Step 4: 写入 Redis 缓存（TTL=86400s）
        try:
            await self._set_to_cache(cache_key, pruned_content, memory_type)
        except Exception as e:
            logger.warning(
                f"[PruningPipeline] Redis 缓存写入失败（不影响主流程）: "
                f"key={cache_key}, err={e}",
                exc_info=True,
            )

        if _pruning_records is not None:
            _pruning_records.append({
                "conversation_id": conversation_id,
                "message_seq": message_seq,
                "source": "llm",
                "input": {
                    "msgs": [
                        {"role": "User", "msg": user_content},
                        {"role": "Assistant", "msg": content},
                    ]
                },
                "gold": {
                    "should_process_user_msg": should_process_user_msg,
                    "processed_user_msg": processed_user_msg,
                    "assistant_memory_hint": pruned_content,
                    "assistant_memory_type": memory_type,
                },
            })

        return pruned_content, should_process_user_msg, processed_user_msg

    def _cache_key(self, conversation_id: str, message_seq: int) -> str:
        """生成 Redis 缓存 key。

        Args:
            conversation_id: 对话 ID
            message_seq: 消息序号

        Returns:
            格式为 pruning:{conversation_id}:{message_seq} 的 key
        """
        return f"pruning:{conversation_id}:{message_seq}"

    # ──────────────────────────────────────────────
    # 内部方法：LLM 剪枝
    # ──────────────────────────────────────────────

    async def _call_llm_prune(self, content: str, user_content: str = "") -> tuple[str, str, bool, Optional[str]]:
        """调用 SemanticPruner 对单条 assistant 消息执行剪枝。

        复用现有 SemanticPruner 逻辑，将"整批消息剪枝"适配为"单条消息剪枝"。
        构造一个 user-assistant 消息对，调用 SemanticPruner.extract_assistant_hint()。

        语言检测：根据输入内容（user_content + content）自动检测语言，
        确保 prompt 指令语言与输入内容语言一致，从而让 LLM 输出跟随输入语言。

        Args:
            content: assistant 消息原始内容
            user_content: 紧邻此 assistant 消息之前的 user 消息内容（可为空）

        Returns:
            (pruned_content, memory_type, should_process_user_msg, processed_user_msg) 元组：
            - pruned_content: 剪枝后内容（A'）；若无记忆价值则为空字符串
            - memory_type: LLM 返回的类型枚举（comfort/suggestion/... 或 NULL）
            - should_process_user_msg: 是否需要对 user 消息做规整
            - processed_user_msg: 规整后的 user 消息文本，不需要处理时为 None
        """
        from app.core.memory.storage_services.extraction_engine.data_preprocessing import (
            SemanticPruner,
        )
        from app.core.memory.models.config_models import PruningConfig
        from app.core.memory.models.message_models import ConversationMessage

        # 确保 LLM 客户端已初始化
        self._ensure_llm_client()

        pruning_config = PruningConfig(
            pruning_switch=True,
            pruning_scene=self.memory_config.pruning_scene or "education",
            pruning_threshold=self.memory_config.pruning_threshold,
            scene_id=(
                str(self.memory_config.scene_id)
                if self.memory_config.scene_id
                else None
            ),
            ontology_class_infos=self.memory_config.ontology_class_infos,
        )

        pruner = SemanticPruner(
            config=pruning_config,
            llm_client=self._llm_client,
            language=self.language,
        )

        # 使用实际的 user 消息（若有），否则用占位符
        user_msg = ConversationMessage(
            role="user",
            msg=user_content if user_content else "[context]",
        )
        asst_msg = ConversationMessage(role="assistant", msg=content)

        result = await pruner.extract_assistant_hint(user_msg, asst_msg)

        if result.assistant_memory_hint == "NULL":
            logger.info("[PruningPipeline] LLM 判断无记忆价值，返回空字符串")
            return "", result.assistant_memory_type, result.should_process_user_msg, result.processed_user_msg

        return result.assistant_memory_hint, result.assistant_memory_type, result.should_process_user_msg, result.processed_user_msg

    # ──────────────────────────────────────────────
    # 内部方法：Neo4j 写入
    # ──────────────────────────────────────────────

    async def _write_to_neo4j(
        self,
        conversation_id: str,
        message_seq: int,
        original_content: str,
        pruned_content: str,
    ) -> None:
        """将剪枝结果写入 Neo4j（AssistantOriginal + AssistantPruned 节点）。

        创建：
        - AssistantOriginal 节点：存储原始 assistant 消息
        - AssistantPruned 节点：存储剪枝后内容（A'）
        - PRUNED_TO 边：连接 Original → Pruned

        Args:
            conversation_id: 对话 ID（用作 dialog_id）
            message_seq: 消息序号（用于节点命名）
            original_content: assistant 消息原始内容
            pruned_content: 剪枝后内容（A'）
        """
        from app.core.memory.models.graph_models import (
            AssistantOriginalNode,
            AssistantPrunedNode,
            AssistantPrunedEdge,
        )
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.repositories.neo4j.cypher_queries import (
            ASSISTANT_ORIGINAL_NODE_SAVE,
            ASSISTANT_PRUNED_NODE_SAVE,
            ASSISTANT_PRUNED_EDGE_SAVE,
        )

        now = datetime.now(timezone.utc)
        run_id = uuid4().hex
        pair_id = uuid4().hex

        # 构建节点 ID（基于 conversation_id + message_seq 保证幂等性）
        original_id = f"orig_{conversation_id}_{message_seq}"
        pruned_id = f"pruned_{conversation_id}_{message_seq}"

        original_node = AssistantOriginalNode(
            id=original_id,
            name=f"AssistantOriginal_{message_seq}",
            end_user_id=self.end_user_id,
            run_id=run_id,
            created_at=now,
            pair_id=pair_id,
            dialog_id=conversation_id,
            text=original_content,
        )

        pruned_node = AssistantPrunedNode(
            id=pruned_id,
            name=f"AssistantPruned_{message_seq}",
            end_user_id=self.end_user_id,
            run_id=run_id,
            created_at=now,
            pair_id=pair_id,
            dialog_id=conversation_id,
            text=pruned_content if pruned_content else "NULL",
            memory_type="NULL",  # 单条剪枝时 memory_type 由 LLM 返回，此处简化为 NULL
        )

        pruned_edge = AssistantPrunedEdge(
            source=original_id,
            target=pruned_id,
            end_user_id=self.end_user_id,
            run_id=run_id,
            created_at=now,
            pair_id=pair_id,
        )

        # 确保 Neo4j 连接器已初始化
        self._ensure_neo4j_connector()

        async def _write_in_transaction(tx):
            # 写入 AssistantOriginal 节点
            original_data = [original_node.model_dump()]
            result = await tx.run(ASSISTANT_ORIGINAL_NODE_SAVE, originals=original_data)
            await result.consume()

            # 写入 AssistantPruned 节点
            pruned_data = [pruned_node.model_dump()]
            result = await tx.run(ASSISTANT_PRUNED_NODE_SAVE, pruneds=pruned_data)
            await result.consume()

            # 写入 PRUNED_TO 边
            edge_data = [{
                "source": pruned_edge.source,
                "target": pruned_edge.target,
                "pair_id": pruned_edge.pair_id,
                "end_user_id": pruned_edge.end_user_id,
                "run_id": pruned_edge.run_id,
                "created_at": to_iso_z(pruned_edge.created_at),
            }]
            result = await tx.run(ASSISTANT_PRUNED_EDGE_SAVE, edges=edge_data)
            await result.consume()

        await self._neo4j_connector.execute_write_transaction(_write_in_transaction)
        logger.info(
            f"[PruningPipeline] Neo4j 写入完成: "
            f"conv={conversation_id}, seq={message_seq}, "
            f"original_id={original_id}, pruned_id={pruned_id}"
        )

    # ──────────────────────────────────────────────
    # 内部方法：Redis 缓存读写
    # ──────────────────────────────────────────────

    async def _get_from_cache(self, cache_key: str) -> tuple[Optional[str], Optional[str]]:
        """从 Redis 缓存读取剪枝结果。

        缓存格式（JSON）：{"hint": "...", "type": "comfort"}

        Args:
            cache_key: Redis key（格式：pruning:{conversation_id}:{message_seq}）

        Returns:
            (hint, memory_type) 元组：
            - hint: 缓存的剪枝内容（A'），不存在或解析失败时为 None
            - memory_type: 缓存的 memory_type（comfort/suggestion/...）
        """
        try:
            from app.aioRedis import get_thread_safe_redis

            redis_client = get_thread_safe_redis()
            value = await redis_client.get(cache_key)
            if value is None:
                return None, None

            obj = json.loads(value)
            if not isinstance(obj, dict):
                return None, None
            return obj.get("hint"), obj.get("type")
        except Exception as e:
            logger.warning(
                f"[PruningPipeline] Redis 读取失败: key={cache_key}, err={e}",
                exc_info=True,
            )
            return None, None

    async def _set_to_cache(
        self,
        cache_key: str,
        pruned_content: str,
        memory_type: Optional[str] = None,
    ) -> None:
        """将剪枝结果写入 Redis 缓存（SETEX，TTL=86400s），格式为 JSON。

        使用 SETEX 确保 TTL 被正确设置，防止缓存永久占用内存。

        Args:
            cache_key: Redis key（格式：pruning:{conversation_id}:{message_seq}）
            pruned_content: 剪枝后内容（A'）
            memory_type: LLM 返回的 memory_type 枚举（comfort/suggestion/... 或 NULL）
        """
        try:
            from app.aioRedis import get_thread_safe_redis

            redis_client = get_thread_safe_redis()
            type_value = memory_type or "NULL"
            payload = json.dumps(
                {"hint": pruned_content, "type": type_value},
                ensure_ascii=False,
            )
            await redis_client.set(cache_key, payload, ex=self.CACHE_TTL)
            logger.info(
                f"[PruningPipeline] Redis 缓存写入: key={cache_key}, "
                f"ttl={self.CACHE_TTL}s, content_len={len(pruned_content)}, "
                f"memory_type={type_value}"
            )
        except Exception as e:
            logger.warning(
                f"[PruningPipeline] Redis 写入失败: key={cache_key}, err={e}",
                exc_info=True,
            )

    # ──────────────────────────────────────────────
    # 辅助方法：客户端初始化
    # ──────────────────────────────────────────────

    def _ensure_llm_client(self) -> None:
        """确保 LLM 客户端已初始化（懒加载）。"""
        if self._llm_client is not None:
            return

        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        from app.db import get_db_context

        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            self._llm_client = factory.get_llm_client_from_config(self.memory_config)

        logger.info("[PruningPipeline] LLM 客户端初始化完成")

    def _ensure_neo4j_connector(self) -> None:
        """确保 Neo4j 连接器已初始化（懒加载）。"""
        if self._neo4j_connector is not None:
            return

        from app.repositories.neo4j.neo4j_connector import Neo4jConnector

        self._neo4j_connector = Neo4jConnector()
        logger.info("[PruningPipeline] Neo4j 连接器初始化完成")

    async def close(self) -> None:
        """释放资源：关闭 Neo4j 连接器。"""
        if self._neo4j_connector is not None:
            try:
                await self._neo4j_connector.close()
            except Exception as e:
                logger.warning(f"[PruningPipeline] Neo4j 连接器关闭失败: {e}")
            finally:
                self._neo4j_connector = None
