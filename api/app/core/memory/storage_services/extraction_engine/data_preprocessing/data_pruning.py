"""
Assistant 消息语义剪枝器

功能：
- 将对话拆分为 User-Assistant 消息对
- 对每个消息对，调用 LLM 从 Assistant 消息中提取记忆摘要
- 若 Assistant 消息无记忆价值（hint=NULL），则删除该 Assistant 消息
- 若有记忆价值，用压缩后的 assistant_memory_hint 替换原始冗长回复
- User 消息始终保留，不做任何修改
- 支持并发 LLM 调用、LRU 缓存、重试与降级
"""

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from datetime import datetime
from typing import List, Optional, Dict
from uuid import uuid4

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

    - assistant_memory_hint: 从 Assistant 消息中提取的极短辅助摘要，无价值时为 "NULL"
    - assistant_memory_type: 摘要类型枚举，无价值时为 "NULL"
    - should_process_user_msg: 是否需要对 User 消息做规整
    - processed_user_msg: 规整后的 User 消息文本，不需要处理时为 null
    """

    assistant_memory_hint: str = Field(
        ..., description="从 Assistant 消息提取的记忆摘要，或 'NULL'"
    )
    assistant_memory_type: str = Field(
        ...,
        description="comfort | suggestion | recommendation | warning | instruction | NULL",
    )
    should_process_user_msg: bool = Field(
        default=False, description="是否需要对 User 消息做规整"
    )
    processed_user_msg: Optional[str] = Field(
        default=None, description="规整后的 User 消息文本，不需要处理时为 null"
    )


class SemanticPruner:
    """Assistant 消息语义剪枝器。

    将对话拆分为 User-Assistant 消息对，通过 LLM 判断 Assistant 消息的记忆价值：
    - 有价值：用压缩摘要替换原始 Assistant 消息
    - 无价值（NULL）：删除该 Assistant 消息
    - User 消息始终保留
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
        self._snapshot = snapshot  # PipelineSnapshot 实例，用于输出剪枝快照

        # 加载 Jinja2 模板
        self.template = prompt_env.get_template("extract_pruning.jinja2")

        # LRU 缓存：避免对相同消息对重复调用 LLM
        self._cache: OrderedDict[str, AssistantPruningResponse] = OrderedDict()
        self._cache_max_size = 1000

        # Snapshot 数据收集：每个消息对的 input + gold
        self._snapshot_records: List[Dict] = []

        # 剪枝记录：用于后续写入 Neo4j（AssistantOriginal + AssistantPruned 节点）
        self.pruning_records: List[AssistantPruningRecord] = []

        # 运行日志
        self.run_logs: List[str] = []

        self._log(
            f"[剪枝-初始化] 场景={self.config.pruning_scene}, "
            f"语言={self.language}, 开关={self.config.pruning_switch}"
        )

    # ──────────────────────────────────────────────
    # 公开接口（保持与旧版兼容）
    # ──────────────────────────────────────────────

    async def prune_dialog(self, dialog: DialogData) -> DialogData:
        """单对话剪枝入口。"""
        if not self.config.pruning_switch:
            return dialog

        msgs = dialog.context.msgs
        kept = await self._prune_messages(msgs, f"对话ID={dialog.id}")
        dialog.context = ConversationContext(msgs=kept)

        # 保存剪枝快照
        self._save_snapshot()

        return dialog

    async def prune_dataset(self, dialogs: List[DialogData]) -> List[DialogData]:
        """数据集层面剪枝入口，逐对话处理。"""
        if not self.config.pruning_switch:
            return dialogs

        self._log(
            f"[剪枝-数据集] 对话总数={len(dialogs)}, "
            f"场景={self.config.pruning_scene}, "
            f"开关={self.config.pruning_switch}"
        )

        result: List[DialogData] = []
        total_original = 0
        total_deleted = 0

        stats = {
            "scene": self.config.pruning_scene,
            "dialog_total": len(dialogs),
            "enabled": self.config.pruning_switch,
            "total_deleted_messages": 0,
            "remaining_dialogs": 0,
            "dialogs": [],
        }

        for d_idx, dd in enumerate(dialogs):
            msgs = dd.context.msgs
            original_count = len(msgs)
            total_original += original_count

            kept = await self._prune_messages(msgs, f"对话 {d_idx + 1}")

            deleted_count = original_count - len(kept)
            total_deleted += deleted_count

            dd.context = ConversationContext(msgs=kept)
            result.append(dd)

            stats["dialogs"].append({
                "index": d_idx + 1,
                "total_messages": original_count,
                "deleted": deleted_count,
                "kept": len(kept),
            })

        stats["total_deleted_messages"] = total_deleted
        stats["remaining_dialogs"] = len(result)

        self._log(f"[剪枝-数据集] 总消息={total_original}, 删除={total_deleted}")

        # 保存统计日志
        self._save_stats(stats)

        # 保存剪枝快照到 PipelineSnapshot
        self._save_snapshot()

        if not result:
            logger.warning("语义剪枝后数据集为空，已回退为未剪枝数据")
            return dialogs

        return result

    # ──────────────────────────────────────────────
    # 核心剪枝逻辑
    # ──────────────────────────────────────────────

    async def _prune_messages(
        self, msgs: List[ConversationMessage], label: str
    ) -> List[ConversationMessage]:
        """对消息列表执行 Assistant 剪枝。

        流程：
        1. 扫描消息，配对 User-Assistant 对
        2. 对每个消息对并发调用 LLM 提取 assistant_memory_hint
        3. hint="NULL" → 删除 Assistant 消息
        4. hint 非 NULL → 用压缩摘要替换 Assistant 原始消息
        5. User 消息、带文件的消息、非配对消息原样保留
        """
        if not msgs:
            return msgs

        # 第一步：识别 User-Assistant 消息对
        pairs = self._pair_user_assistant(msgs)
        # pairs: List[(user_idx, assistant_idx)]

        # 第二步：并发调用 LLM 处理每个消息对
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_pair(user_idx: int, asst_idx: int):
            async with semaphore:
                user_msg = msgs[user_idx]
                asst_msg = msgs[asst_idx]

                # 构建 snapshot 的 input 部分
                input_record = {
                    "msgs": [
                        {"role": "User", "msg": user_msg.msg},
                        {"role": "Assistant", "msg": asst_msg.msg},
                    ]
                }

                # 带文件的 Assistant 消息不剪枝
                if message_has_files(asst_msg):
                    self._log(
                        f"  [{label}] 索引{asst_idx} 带文件，跳过剪枝"
                    )
                    self._snapshot_records.append({
                        "input": input_record,
                        "gold": {
                            "assistant_memory_hint": asst_msg.msg,
                            "assistant_memory_type": "skipped (has files)",
                            "should_process_user_msg": False,
                            "processed_user_msg": None,
                        },
                    })
                    return user_idx, asst_idx, asst_msg.msg, False, False, None

                result = await self._extract_assistant_hint(user_msg, asst_msg)

                # 收集 snapshot 记录（包含 user 侧处理结果）
                self._snapshot_records.append({
                    "input": input_record,
                    "output": {
                        "should_process_user_msg": result.should_process_user_msg,
                        "processed_user_msg": result.processed_user_msg,
                        "assistant_memory_hint": result.assistant_memory_hint,
                        "assistant_memory_type": result.assistant_memory_type,
                    },
                })

                # 收集剪枝记录（用于后续写入 Neo4j）
                self.pruning_records.append(AssistantPruningRecord(
                    pair_id=uuid4().hex,
                    original_text=asst_msg.msg,
                    pruned_text=result.assistant_memory_hint,
                    memory_type=result.assistant_memory_type,
                    created_at=datetime.now().isoformat(),
                ))

                if result.assistant_memory_hint == "NULL":
                    self._log(
                        f"  [{label}] 索引{asst_idx} → NULL，删除 "
                        f"('{asst_msg.msg[:40]}')"
                    )
                    asst_new_text = None
                    asst_should_delete = True
                else:
                    self._log(
                        f"  [{label}] 索引{asst_idx} → "
                        f"type={result.assistant_memory_type}, "
                        f"hint='{result.assistant_memory_hint[:50]}'"
                    )
                    asst_new_text = result.assistant_memory_hint
                    asst_should_delete = False

                # user 侧处理结果
                user_should_replace = (
                    result.should_process_user_msg
                    and result.processed_user_msg is not None
                    and result.processed_user_msg.strip() != ""
                )
                if user_should_replace:
                    self._log(
                        f"  [{label}] 索引{user_idx} User → 规整: "
                        f"'{result.processed_user_msg[:50]}'"
                    )

                return (
                    user_idx,
                    asst_idx,
                    asst_new_text,
                    asst_should_delete,
                    user_should_replace,
                    result.processed_user_msg if user_should_replace else None,
                )

        tasks = [process_pair(u, a) for u, a in pairs]
        pair_results = await asyncio.gather(*tasks)

        # 构建替换/删除映射
        # asst_idx → (new_msg_text | None)
        asst_actions: Dict[int, Optional[str]] = {}
        # user_idx → new_msg_text
        user_actions: Dict[int, str] = {}

        for user_idx, asst_idx, asst_new_text, asst_should_delete, user_should_replace, user_new_text in pair_results:
            if asst_should_delete:
                asst_actions[asst_idx] = None
            else:
                asst_actions[asst_idx] = asst_new_text
            if user_should_replace and user_new_text is not None:
                user_actions[user_idx] = user_new_text

        # 第三步：构建最终消息列表
        kept: List[ConversationMessage] = []
        for idx, m in enumerate(msgs):
            if idx in asst_actions:
                new_text = asst_actions[idx]
                if new_text is None:
                    # 删除该 Assistant 消息
                    continue
                else:
                    # 用压缩摘要替换原始消息
                    kept.append(ConversationMessage(
                        role=m.role,
                        msg=new_text,
                        files=m.files,
                    ))
            elif idx in user_actions:
                # 用规整后的文本替换 User 消息
                kept.append(ConversationMessage(
                    role=m.role,
                    msg=user_actions[idx],
                    files=m.files,
                ))
            else:
                # User 消息、未配对的消息原样保留
                kept.append(m)

        # 兜底：至少保留 1 条消息
        if not kept and msgs:
            kept = [msgs[0]]

        deleted = len(msgs) - len(kept)
        self._log(
            f"[剪枝] {label} 总消息={len(msgs)}, "
            f"配对数={len(pairs)}, 删除={deleted}, 保留={len(kept)}"
        )
        return kept

    def _pair_user_assistant(
        self, msgs: List[ConversationMessage]
    ) -> List[tuple]:
        """将消息列表中相邻的 User-Assistant 配对。

        规则：
        - 遍历消息，遇到 role=user 时记录索引
        - 紧接着的 role=assistant 消息与之配对
        - 连续多条 user 消息只取最后一条作为上下文
        - 未配对的 assistant 消息（如对话开头就是 assistant）不处理
        """
        pairs = []
        last_user_idx = None

        for idx, m in enumerate(msgs):
            if m.role == "user":
                last_user_idx = idx
            elif m.role == "assistant" and last_user_idx is not None:
                pairs.append((last_user_idx, idx))
                last_user_idx = None  # 一个 user 只配一个 assistant

        return pairs

    # ──────────────────────────────────────────────
    # LLM 调用
    # ──────────────────────────────────────────────

    async def _extract_assistant_hint(
        self,
        user_msg: ConversationMessage,
        asst_msg: ConversationMessage,
    ) -> AssistantPruningResponse:
        """调用 LLM 从 User-Assistant 消息对中提取 Assistant 记忆摘要。

        使用 extract_pruning.jinja2 模板，输入格式：
        {"msgs": [{"role": "User", "msg": "..."}, {"role": "Assistant", "msg": "..."}]}
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

        # 缓存检查
        cache_key = hashlib.sha1(dialog_text.encode("utf-8")).hexdigest()
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        # LRU 淘汰
        if len(self._cache) >= self._cache_max_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

        # 渲染模板
        rendered = self.template.render(dialog_text=dialog_text)
        log_template_rendering("extract_pruning.jinja2", {
            "language": self.language,
        })
        log_prompt_rendering("pruning-assistant-hint", rendered)

        if not self.llm_client:
            raise RuntimeError("llm_client 未配置；请配置 LLM 以进行 Assistant 剪枝。")

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
                result = await self.llm_client.response_structured(
                    messages, AssistantPruningResponse
                )
                self._cache[cache_key] = result
                return result
            except Exception as e:
                if attempt < max_retries - 1:
                    self._log(
                        f"[剪枝-LLM] 第 {attempt + 1} 次尝试失败，重试: "
                        f"{str(e)[:100]}"
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    # 降级：保留原始消息，不剪枝
                    self._log(
                        f"[剪枝-LLM] {max_retries} 次失败，降级保留原始消息"
                    )
                    return AssistantPruningResponse(
                        assistant_memory_hint=asst_msg.msg,
                        assistant_memory_type="NULL",
                    )

    # ──────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────

    def _save_stats(self, stats: dict) -> None:
        """保存剪枝统计到文件。"""
        try:
            from app.core.config import settings

            settings.ensure_memory_output_dir()
            log_output_path = settings.get_memory_output_path("pruned_terminal.json")
            with open(log_output_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"[剪枝] 保存统计日志失败：{e}")

    def _save_snapshot(self) -> None:
        """将剪枝结果保存到 PipelineSnapshot（1_assistant_pruning.json）。

        输出格式：每个 User-Assistant 消息对一条记录，包含：
        - input.msgs: 原始消息对 [{role, msg}, {role, msg}]
        - gold.assistant_memory_hint: LLM 提取的记忆摘要
        - gold.assistant_memory_type: 摘要类型枚举
        """
        if not self._snapshot or not self._snapshot_records:
            return

        try:
            self._snapshot.save_stage("1_assistant_pruning", self._snapshot_records)
            self._log(
                f"[剪枝-快照] 已保存 {len(self._snapshot_records)} 条记录 "
                f"到 1_assistant_pruning.json"
            )
        except Exception as e:
            self._log(f"[剪枝-快照] 保存失败: {e}")

    def _log(self, msg: str) -> None:
        """记录日志。"""
        try:
            self.run_logs.append(msg)
        except Exception:
            pass
        logger.debug(msg)
