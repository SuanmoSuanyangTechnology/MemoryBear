"""
语义剪枝器

功能：
- 对单条消息调用 LLM 提取记忆摘要（extract_assistant_hint）
- 支持批量对话剪枝（prune_dataset，供 pilot_run 使用）
- 重试与降级机制
"""

import asyncio
import hashlib
import json
import logging
from typing import List, Optional, Dict

from pydantic import BaseModel, Field

from app.core.memory.models.message_models import (
    DialogData,
    ConversationMessage,
    ConversationContext,
)
from app.core.memory.models.config_models import PruningConfig
from app.core.memory.utils.prompt.prompt_utils import (
    prompt_env,
    log_prompt_rendering,
    log_template_rendering,
)

logger = logging.getLogger(__name__)


def message_has_files(message: "ConversationMessage") -> bool:
    """检查消息是否包含文件。"""
    return message.files and len(message.files) > 0


class AssistantPruningRecord(BaseModel):
    """单个 User-Assistant 消息对的剪枝记录，用于后续写入 Neo4j。"""

    pair_id: str = Field(..., description="唯一配对 ID，Original 和 Pruned 节点共享")
    original_text: str = Field(..., description="Assistant 原始回复全文")
    pruned_text: str = Field(..., description="剪枝后文本（assistant_memory_hint），或 'NULL'")
    memory_type: str = Field(..., description="comfort|suggestion|recommendation|warning|instruction|NULL")
    created_at: str = Field(..., description="ISO 时间戳")


class AssistantPruningResponse(BaseModel):
    """LLM 对单个 User-Assistant 消息对的剪枝结果。

    - assistant_memory_hint: 从 Assistant 消息中提取的极短辅助摘要，无价值时为 "NULL"，
      Assistant 为空时为 null
    - assistant_memory_type: 摘要类型枚举，无价值时为 "NULL"，Assistant 为空时为 null
    - should_process_user_msg: 是否需要对 User 消息做规整，User 为空时为 null
    - processed_user_msg: 规整后的 User 消息文本，不需要处理时为 null
    """

    assistant_memory_hint: Optional[str] = Field(
        default=None, description="从 Assistant 消息提取的记忆摘要，或 'NULL'，Assistant 为空时为 null"
    )
    assistant_memory_type: Optional[str] = Field(
        default=None,
        description="comfort | suggestion | recommendation | warning | instruction | NULL，Assistant 为空时为 null",
    )
    should_process_user_msg: Optional[bool] = Field(
        default=None, description="是否需要对 User 消息做规整，User 为空时为 null"
    )
    processed_user_msg: Optional[str] = Field(
        default=None, description="规整后的 User 消息文本，不需要处理时为 null"
    )


class SemanticPruner:
    """语义剪枝器。

    核心方法：
    - extract_assistant_hint(): 单条 (User, Assistant) 对的 LLM 剪枝调用
    - prune_dataset(): 批量对话剪枝（供 pilot_run 使用）
    """

    def __init__(
        self,
        config: Optional[PruningConfig] = None,
        llm_client=None,
        language: str = "zh",
        max_concurrent: int = 5,
        snapshot=None,
    ):
        if config is None:
            config = PruningConfig(
                pruning_switch=False,
                pruning_scene="education",
                pruning_threshold=0.5,
            )

        self.config = config
        self.llm_client = llm_client
        self.language = language
        self.max_concurrent = max_concurrent
        self._snapshot = snapshot

        # 加载 Jinja2 模板
        self.template = prompt_env.get_template("extract_pruning.jinja2")

    # ──────────────────────────────────────────────
    # 核心接口：单条 LLM 调用
    # ──────────────────────────────────────────────

    async def extract_assistant_hint(
        self,
        user_msg: ConversationMessage,
        asst_msg: ConversationMessage,
    ) -> AssistantPruningResponse:
        """调用 LLM 从 User-Assistant 消息对中提取 Assistant 记忆摘要。

        使用 extract_pruning.jinja2 模板，输入格式：
        {"msgs": [{"role": "User", "msg": "..."}, {"role": "Assistant", "msg": "..."}]}

        Args:
            user_msg: User 消息（可为空内容，触发 prompt 空侧规则）
            asst_msg: Assistant 消息（可为空内容，触发 prompt 空侧规则）

        Returns:
            AssistantPruningResponse 包含 assistant 摘要和 user 规整结果
        """
        # 构建模板输入
        dialog_text = json.dumps(
            {
                "msgs": [
                    {"role": "User", "msg": user_msg.msg},
                    {"role": "Assistant", "msg": asst_msg.msg},
                ]
            },
            ensure_ascii=False,
        )

        # 渲染模板
        rendered = self.template.render(
            dialog_text=dialog_text,
            language=self.language,
        )
        log_template_rendering("extract_pruning.jinja2", {"language": self.language})
        log_prompt_rendering("pruning-assistant-hint", rendered)

        if not self.llm_client:
            raise RuntimeError("llm_client 未配置；请配置 LLM 以进行剪枝。")

        messages = [
            {
                "role": "system",
                "content": "你是一个面向记忆存储的辅助信息提取器，只输出严格 JSON。",
            },
            {"role": "user", "content": rendered},
        ]

        # 重试机制
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await self.llm_client.call_structured(messages, AssistantPruningResponse)
                return result
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"[剪枝-LLM] 第 {attempt + 1} 次尝试失败，重试: {str(e)[:100]}"
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    # 降级：保留原始消息，不剪枝
                    logger.warning(f"[剪枝-LLM] {max_retries} 次失败，降级保留原始消息")
                    return AssistantPruningResponse(
                        assistant_memory_hint=asst_msg.msg,
                        assistant_memory_type="NULL",
                    )

    # ──────────────────────────────────────────────
    # 批量接口（供 pilot_run 使用）在v0.3.13将修改
    # ──────────────────────────────────────────────

    async def prune_dataset(self, dialogs: List[DialogData]) -> List[DialogData]:
        """数据集层面剪枝入口，逐对话处理。供 pilot_run_service 使用。"""
        if not self.config.pruning_switch:
            return dialogs

        result: List[DialogData] = []
        for dd in dialogs:
            msgs = dd.context.msgs
            kept = await self._prune_messages(msgs)
            dd.context = ConversationContext(msgs=kept)
            result.append(dd)

        if not result:
            return dialogs
        return result

    async def _prune_messages(
        self, msgs: List[ConversationMessage]
    ) -> List[ConversationMessage]:
        """对消息列表执行 Assistant 剪枝（内部方法）。"""
        if not msgs:
            return msgs

        pairs = self._pair_user_assistant(msgs)
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_pair(user_idx: int, asst_idx: int):
            async with semaphore:
                user_msg = msgs[user_idx]
                asst_msg = msgs[asst_idx]

                if message_has_files(asst_msg):
                    return user_idx, asst_idx, asst_msg.msg, False, False, None

                result = await self.extract_assistant_hint(user_msg, asst_msg)

                if result.assistant_memory_hint == "NULL" or result.assistant_memory_hint is None:
                    return user_idx, asst_idx, None, True, False, None
                else:
                    user_should_replace = (
                        result.should_process_user_msg
                        and result.processed_user_msg is not None
                        and result.processed_user_msg.strip() != ""
                    )
                    return (
                        user_idx, asst_idx,
                        result.assistant_memory_hint, False,
                        user_should_replace,
                        result.processed_user_msg if user_should_replace else None,
                    )

        tasks = [process_pair(u, a) for u, a in pairs]
        pair_results = await asyncio.gather(*tasks)

        asst_actions: Dict[int, Optional[str]] = {}
        user_actions: Dict[int, str] = {}

        for user_idx, asst_idx, asst_new_text, asst_should_delete, user_should_replace, user_new_text in pair_results:
            if asst_should_delete:
                asst_actions[asst_idx] = None
            else:
                asst_actions[asst_idx] = asst_new_text
            if user_should_replace and user_new_text is not None:
                user_actions[user_idx] = user_new_text

        kept: List[ConversationMessage] = []
        for idx, m in enumerate(msgs):
            if idx in asst_actions:
                new_text = asst_actions[idx]
                if new_text is None:
                    continue
                else:
                    kept.append(ConversationMessage(
                        role=m.role, msg=new_text, dialog_at=m.dialog_at, files=m.files,
                    ))
            elif idx in user_actions:
                kept.append(ConversationMessage(
                    role=m.role, msg=user_actions[idx], dialog_at=m.dialog_at, files=m.files,
                ))
            else:
                kept.append(m)

        if not kept and msgs:
            kept = [msgs[0]]

        return kept

    def _pair_user_assistant(self, msgs: List[ConversationMessage]) -> List[tuple]:
        """将消息列表中相邻的 User-Assistant 配对。"""
        pairs = []
        last_user_idx = None
        for idx, m in enumerate(msgs):
            if m.role == "user":
                last_user_idx = idx
            elif m.role == "assistant" and last_user_idx is not None:
                pairs.append((last_user_idx, idx))
                last_user_idx = None
        return pairs
