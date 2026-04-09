"""
Metadata extractor module.

Collects user-related statements from post-dedup graph data and
extracts user metadata via an independent LLM call.
"""

import logging
from typing import List, Optional

from app.core.memory.models.graph_models import (
    ExtractedEntityNode,
    StatementEntityEdge,
    StatementNode,
)
from app.core.memory.models.metadata_models import (
    UserMetadata,
)
from app.core.memory.models.message_models import DialogData

logger = logging.getLogger(__name__)

# Reuse the same user-entity detection logic from dedup module
_USER_NAMES = {"用户", "我", "user", "i"}
_CANONICAL_USER_TYPE = "用户"


def _is_user_entity(ent: ExtractedEntityNode) -> bool:
    """判断实体是否为用户实体"""
    name = (getattr(ent, "name", "") or "").strip().lower()
    etype = (getattr(ent, "entity_type", "") or "").strip()
    return name in _USER_NAMES or etype == _CANONICAL_USER_TYPE


class MetadataExtractor:
    """Extracts user metadata from post-dedup graph data via independent LLM call."""

    def __init__(self, llm_client, language: str = "zh"):
        self.llm_client = llm_client
        self.language = language

    @staticmethod
    def detect_language(statements: List[str]) -> str:
        """根据 statement 文本内容检测语言。
        如果文本中包含中文字符则返回 "zh"，否则返回 "en"。
        """
        import re
        combined = " ".join(statements)
        if re.search(r'[\u4e00-\u9fff]', combined):
            return "zh"
        return "en"

    # def collect_user_raw_messages(
    #     self,
    #     dialog_data_list: List[DialogData],
    # ) -> List[str]:
    #     """
    #     从原始对话数据中提取 speaker="user" 的消息原文。

    #     直接使用用户的原始输入，不经过陈述句提取阶段的 LLM 改写，
    #     避免第一人称被替换为第三人称导致元数据/别名提取错误。

    #     Returns:
    #         用户原始消息文本列表
    #     """
    #     result = []
    #     for dialog in dialog_data_list:
    #         if not dialog.context or not dialog.context.msgs:
    #             continue
    #         for msg in dialog.context.msgs:
    #             if getattr(msg, 'role', '') == 'user':
    #                 text = (getattr(msg, 'msg', '') or '').strip()
    #                 if text:
    #                     result.append(text)

    #     logger.info(f"收集到 {len(result)} 条用户原始消息")
    #     if result:
    #         for i, text in enumerate(result):
    #             logger.info(f"  [user message {i+1}] {text[:200]}")
    #     return result

    def collect_user_related_statements(
        self,
        entity_nodes: List[ExtractedEntityNode],
        statement_nodes: List[StatementNode],
        statement_entity_edges: List[StatementEntityEdge],
    ) -> List[str]:
        """
        从去重后的数据中筛选与用户直接相关且由用户发言的 statement 文本。

        筛选逻辑：
        1. 用户实体 → StatementEntityEdge → statement（直接关联）
        2. 只保留 speaker="user" 的 statement（过滤 assistant 回复的噪声）

        Returns:
            用户发言的 statement 文本列表
        """
        # Find user entity IDs
        user_entity_ids = set()
        for ent in entity_nodes:
            if _is_user_entity(ent):
                user_entity_ids.add(ent.id)

        if not user_entity_ids:
            logger.debug("未找到用户实体节点，跳过 statement 收集")
            return []

        # 用户实体 → StatementEntityEdge → statement
        target_stmt_ids = set()
        for edge in statement_entity_edges:
            if edge.target in user_entity_ids:
                target_stmt_ids.add(edge.source)

        # Collect: only speaker="user" statements, preserving order
        result = []
        seen = set()
        total_associated = 0
        skipped_non_user = 0
        for stmt_node in statement_nodes:
            if stmt_node.id in target_stmt_ids and stmt_node.id not in seen:
                total_associated += 1
                speaker = getattr(stmt_node, 'speaker', None) or 'unknown'
                if speaker == "user":
                    text = (stmt_node.statement or "").strip()
                    if text:
                        result.append(text)
                else:
                    skipped_non_user += 1
                seen.add(stmt_node.id)

        logger.info(
            f"收集到 {len(result)} 条用户发言 statement "
            f"(直接关联: {total_associated}, speaker=user: {len(result)}, "
            f"跳过非user: {skipped_non_user})"
        )
        if result:
            for i, text in enumerate(result):
                logger.info(f"  [user statement {i+1}] {text}")
        if total_associated > 0 and len(result) == 0:
            logger.warning(
                f"有 {total_associated} 条直接关联 statement 但全部被 speaker 过滤，"
                f"可能本次写入不包含 user 消息"
            )
        return result

    async def extract_metadata(
        self,
        statements: List[str],
        existing_metadata: Optional[dict] = None,
        existing_aliases: Optional[List[str]] = None,
    ) -> Optional[tuple]:
        """
        对筛选后的 statement 列表调用 LLM 提取元数据和用户别名。

        Args:
            statements: 用户发言的 statement 文本列表
            existing_metadata: 数据库已有的元数据（可选）
            existing_aliases: 数据库已有的用户别名列表（可选）

        Returns:
            (UserMetadata, List[str], List[str]) tuple: (metadata, aliases_to_add, aliases_to_remove) on success, None on failure
        """
        if not statements:
            return None

        try:
            from app.core.memory.utils.prompt.prompt_utils import prompt_env

            detected_language = self.detect_language(statements)
            logger.info(f"元数据提取语言检测结果: {detected_language}")

            template = prompt_env.get_template("extract_user_metadata.jinja2")
            prompt = template.render(
                statements=statements,
                language=detected_language,
                existing_metadata=existing_metadata,
                existing_aliases=existing_aliases,
                json_schema="",
            )

            from app.core.memory.models.metadata_models import MetadataExtractionResponse
            response = await self.llm_client.response_structured(
                messages=[{"role": "user", "content": prompt}],
                response_model=MetadataExtractionResponse,
            )

            if response:
                metadata = response.user_metadata if response.user_metadata else None
                to_add = response.aliases_to_add if response.aliases_to_add else []
                to_remove = response.aliases_to_remove if response.aliases_to_remove else []
                return metadata, to_add, to_remove

            logger.warning("LLM 返回的响应为空")
            return None

        except Exception as e:
            logger.error(f"元数据提取 LLM 调用失败: {e}", exc_info=True)
            return None
