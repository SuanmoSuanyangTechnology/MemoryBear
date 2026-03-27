"""
语义剪枝器 - 在预处理与分块之间过滤与场景不相关内容

功能：
- 对话级一次性抽取判定相关性
- 仅对"不相关对话"的消息按比例删除
- 重要信息（时间、编号、金额、联系方式、地址等）优先保留
- 改进版：增强重要性判断、智能填充消息识别、问答对保护、并发优化
"""

import asyncio
import logging
import os
import hashlib
import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import List, Optional, Dict, Tuple, Set
from pydantic import BaseModel, Field

from app.core.memory.models.message_models import DialogData, ConversationMessage, ConversationContext
from app.core.memory.models.config_models import PruningConfig
from app.core.memory.utils.prompt.prompt_utils import prompt_env, log_prompt_rendering, log_template_rendering
from app.core.memory.storage_services.extraction_engine.data_preprocessing.scene_config import (
    SceneConfigRegistry,
    ScenePatterns
)

logger = logging.getLogger(__name__)


def message_has_files(message: "ConversationMessage") -> bool:
    """检查消息是否包含文件。
    
    Args:
        message: 待检查的消息对象
        
    Returns:
        bool: 如果消息包含文件则返回 True，否则返回 False
    """
    return message.files and len(message.files) > 0


class DialogExtractionResponse(BaseModel):
    """对话级一次性抽取的结构化返回，用于加速剪枝。

    - is_related：对话与场景的相关性判定。
    - times / ids / amounts / contacts / addresses / keywords：重要信息片段，用来在不相关对话中保留关键消息。
    - preserve_keywords：情绪/兴趣/爱好/个人观点相关词，包含这些词的消息必须强制保留。
    - scene_unrelated_snippets：与当前场景无关且无语义关联的消息片段（原文截取），
      用于高阈值阶段精准删除跨场景内容。
    """
    is_related: bool = Field(...)
    times: List[str] = Field(default_factory=list)
    ids: List[str] = Field(default_factory=list)
    amounts: List[str] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    addresses: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    preserve_keywords: List[str] = Field(default_factory=list, description="情绪/兴趣/爱好/个人观点相关词，包含这些词的消息强制保留")
    scene_unrelated_snippets: List[str] = Field(default_factory=list,description="与当前场景无关且无语义关联的消息原文片段，高阈值阶段用于精准删除跨场景内容")


class MessageImportanceResponse(BaseModel):
    """消息重要性批量判断的结构化返回（用于LLM语义判断）。
    
    - importance_scores: 消息索引到重要性分数的映射 (0-10分)
    - reasons: 可选的判断理由
    """
    importance_scores: Dict[int, int] = Field(default_factory=dict, description="消息索引到重要性分数(0-10)的映射")
    reasons: Optional[Dict[int, str]] = Field(default_factory=dict, description="可选的判断理由")


class QAPair(BaseModel):
    """问答对模型，用于识别和保护对话中的问答结构。"""
    question_idx: int = Field(..., description="问题消息的索引")
    answer_idx: int = Field(..., description="答案消息的索引")
    confidence: float = Field(default=1.0, description="问答对的置信度(0-1)")


class SemanticPruner:
    """语义剪枝：在预处理与分块之间过滤与场景不相关内容。

    采用对话级一次性抽取判定相关性；仅对"不相关对话"的消息按比例删除，
    重要信息（时间、编号、金额、联系方式、地址等）优先保留。
    """

    def __init__(self, config: Optional[PruningConfig] = None, llm_client=None, language: str = "zh", max_concurrent: int = 5):
        # 如果没有提供config，使用默认配置
        if config is None:
            # 使用默认的剪枝配置
            config = PruningConfig(
                pruning_switch=False,  # 默认关闭剪枝，保持向后兼容
                pruning_scene="education",
                pruning_threshold=0.5
            )
        
        self.config = config
        self.llm_client = llm_client
        self.language = language  # 保存语言配置
        self.max_concurrent = max_concurrent  # 新增：最大并发数
        
        # 详细日志配置：限制逐条消息日志的数量
        self._detailed_prune_logging = True  # 是否启用详细日志
        self._max_debug_msgs_per_dialog = 20  # 每个对话最多记录前N条消息的详细日志
        
        # 加载统一填充词库
        self.scene_config: ScenePatterns = SceneConfigRegistry.get_config(self.config.pruning_scene)
        
        # 本体类型列表：直接使用 ontology_class_infos（name + description）
        self._ontology_class_infos = getattr(self.config, "ontology_class_infos", None) or []
        # _ontology_classes 仅用于日志统计
        self._ontology_classes = [info.class_name for info in self._ontology_class_infos]
        
        self._log(f"[剪枝-初始化] 场景={self.config.pruning_scene}")
        if self._ontology_class_infos:
            self._log(f"[剪枝-初始化] 注入本体类型({len(self._ontology_class_infos)}个): {self._ontology_classes}")
        else:
            self._log(f"[剪枝-初始化] 未找到本体类型，将使用通用提示词")
        
        # Load Jinja2 template
        self.template = prompt_env.get_template("extracat_Pruning.jinja2")
        
        # 对话抽取缓存：使用 OrderedDict 实现 LRU 缓存
        self._dialog_extract_cache: OrderedDict[str, DialogExtractionResponse] = OrderedDict()
        self._cache_max_size = 1000  # 缓存大小限制
        
        # 运行日志：收集关键终端输出，便于写入 JSON
        self.run_logs: List[str] = []

    # _is_important_message 和 _importance_score 已移除：
    # 重要性判断完全由 extracat_Pruning.jinja2 提示词 + LLM 的 preserve_tokens 机制承担。
    # LLM 根据注入的本体工程类型语义识别需要保护的内容，无需硬编码正则规则。

    def _is_filler_message(self, message: ConversationMessage) -> bool:
        """检测典型寒暄/口头禅/确认类短消息。

        判断顺序：
        1. 空消息
        2. 场景特定填充词库精确匹配
        3. 常见寒暄精确匹配
        4. 组合寒暄模式（前缀 + 后缀组合，如"好的谢谢"、"同学你好"、"明白了"）
        5. 纯表情/标点
        """
        t = message.msg.strip()
        if not t:
            return True

        # 检查是否在场景特定填充词库中（精确匹配）
        if t in self.scene_config.filler_phrases:
            return True

        # 常见寒暄和问候（精确匹配，避免误删）
        common_greetings = {
            "在吗", "在不在", "在呢", "在的",
            "你好", "您好", "hello", "hi",
            "拜拜", "再见", "拜", "88", "bye",
            "好的", "好", "行", "可以", "嗯", "哦", "啊",
            "是的", "对", "对的", "没错", "是啊",
            "哈哈", "呵呵", "嘿嘿", "嗯嗯"
        }
        if t in common_greetings:
            return True

        # 组合寒暄模式：短消息（≤15字）且完全由寒暄成分构成
        # 策略：将消息拆分后，每个片段都能在填充词库或常见寒暄中找到，则整体为填充
        if len(t) <= 15:
            # 确认+称呼/感谢组合，如"好的谢谢"、"明白了"、"知道了谢谢"
            _confirm_prefixes = {"好的", "好", "嗯", "嗯嗯", "哦", "明白", "明白了", "知道了", "了解", "收到", "没问题"}
            _thanks_suffixes = {"谢谢", "谢谢你", "谢谢您", "多谢", "感谢", "谢了"}
            _greeting_suffixes = {"你好", "您好", "老师好", "同学好", "大家好"}
            _greeting_prefixes = {"同学", "老师", "您好", "你好"}
            _close_patterns = {
                "没有了", "没事了", "没问题了", "好了", "行了", "可以了",
                "不用了", "不需要了", "就这样", "就这样吧", "那就这样",
            }
            _polite_responses = {
                "不客气", "不用谢", "没关系", "没事", "应该的", "这是我应该做的",
            }

            # 规则1：确认词 + 感谢词（如"好的谢谢"、"嗯谢谢"）
            for cp in _confirm_prefixes:
                for ts in _thanks_suffixes:
                    if t == cp + ts or t == cp + "，" + ts or t == cp + "," + ts:
                        return True

            # 规则2：称呼前缀 + 问候（如"同学你好"、"老师好"）
            for gp in _greeting_prefixes:
                for gs in _greeting_suffixes:
                    if t == gp + gs or t.startswith(gp) and t.endswith("好"):
                        return True

            # 规则3：结束语 + 感谢（如"没有了，谢谢老师"、"没有了谢谢"）
            for cp in _close_patterns:
                if t.startswith(cp):
                    remainder = t[len(cp):].lstrip("，,、 ")
                    if not remainder or any(remainder.startswith(ts) for ts in _thanks_suffixes):
                        return True

            # 规则4：礼貌回应（如"不客气，祝你考试顺利"——前缀是礼貌词，后半是祝福套话）
            for pr in _polite_responses:
                if t.startswith(pr):
                    remainder = t[len(pr):].lstrip("，,、 ")
                    # 后半是祝福/套话（不含实质信息）
                    if not remainder or re.match(r"^(祝|希望|期待|加油|顺利|好好|保重)", remainder):
                        return True

            # 规则5：纯确认词加"了"后缀（如"明白了"、"知道了"、"好了"）
            _confirm_base = {"明白", "知道", "了解", "收到", "好", "行", "可以", "没问题"}
            for cb in _confirm_base:
                if t == cb + "了" or t == cb + "了。" or t == cb + "了！":
                    return True

        # 检查是否为纯表情符号（方括号包裹）
        if re.fullmatch(r"(\[[^\]]+\])+", t):
            return True
        
        # 纯标点符号
        if re.fullmatch(r"[。！？,.!?…·\s]+", t):
            return True
        
        return False
    
    async def _batch_evaluate_importance_with_llm(
        self, 
        messages: List[ConversationMessage],
        context: str = ""
    ) -> Dict[int, int]:
        """使用LLM批量评估消息的重要性（语义层面）。
        
        Args:
            messages: 消息列表
            context: 对话上下文（可选）
            
        Returns:
            消息索引到重要性分数(0-10)的映射
        """
        if not self.llm_client or not messages:
            return {}
        
        # 构建批量评估的提示词
        msg_list = []
        for idx, msg in enumerate(messages):
            msg_list.append(f"{idx}. {msg.msg}")
        
        msg_text = "\n".join(msg_list)
        
        prompt = f"""请评估以下消息的重要性，给每条消息打分（0-10分）：
- 0-2分：无意义的寒暄、口头禅、纯表情
- 3-5分：一般性对话，有一定信息量但不关键
- 6-8分：包含重要信息（时间、地点、人物、事件等）
- 9-10分：关键决策、承诺、重要数据

对话上下文：
{context if context else "无"}

待评估的消息：
{msg_text}

请以JSON格式返回，格式为：
{{
  "importance_scores": {{
    "0": 分数,
    "1": 分数,
    ...
  }}
}}
"""
        
        try:
            messages_for_llm = [
                {"role": "system", "content": "你是一个专业的对话分析助手，擅长评估消息的重要性。"},
                {"role": "user", "content": prompt}
            ]
            
            response = await self.llm_client.response_structured(
                messages_for_llm,
                MessageImportanceResponse
            )
            
            # 转换字符串键为整数键
            return {int(k): v for k, v in response.importance_scores.items()}
        except Exception as e:
            self._log(f"[剪枝-LLM] 批量重要性评估失败: {str(e)[:100]}")
            return {}
    
    def _identify_qa_pairs(self, messages: List[ConversationMessage]) -> List[QAPair]:
        """识别对话中的问答对，用于保护问答结构的完整性。
        
        改进版：使用场景特定的问句关键词，并排除寒暄类问句
        
        Args:
            messages: 消息列表
            
        Returns:
            问答对列表
        """
        qa_pairs = []
        
        # 寒暄类问句，不应该被保护（这些不是真正的问答）
        greeting_questions = {
            "在吗", "在不在", "你好吗", "怎么样", "好吗",
            "有空吗", "忙吗", "睡了吗", "起床了吗"
        }
        
        for i in range(len(messages) - 1):
            current_msg = messages[i].msg.strip()
            next_msg = messages[i + 1].msg.strip()
            
            # 排除寒暄类问句
            if current_msg in greeting_questions:
                continue
            
            # 使用场景特定的问句关键词，但要求更严格
            is_question = False
            
            # 1. 以问号结尾
            if current_msg.endswith("？") or current_msg.endswith("?"):
                is_question = True
            # 2. 包含实质性问句关键词（排除"吗"这种太宽泛的）
            elif any(word in current_msg for word in ["什么", "为什么", "怎么", "如何", "哪里", "哪个", "谁", "多少", "几点", "何时"]):
                is_question = True
            
            if is_question and next_msg:
                # 检查下一条消息是否像答案（不是另一个问句，也不是寒暄）
                is_answer = not (next_msg.endswith("？") or next_msg.endswith("?"))
                
                # 排除寒暄类回复
                greeting_answers = {"你好", "您好", "在呢", "在的", "嗯", "哦", "好的"}
                if next_msg in greeting_answers:
                    is_answer = False
                
                if is_answer:
                    qa_pairs.append(QAPair(
                        question_idx=i,
                        answer_idx=i + 1,
                        confidence=0.8  # 基于规则的置信度
                    ))
        
        return qa_pairs
    
    def _get_protected_indices(
        self, 
        messages: List[ConversationMessage],
        qa_pairs: List[QAPair],
        window_size: int = 2
    ) -> Set[int]:
        """获取需要保护的消息索引集合（问答对+上下文窗口）。
        
        Args:
            messages: 消息列表
            qa_pairs: 问答对列表
            window_size: 上下文窗口大小（前后各保留几条消息）
            
        Returns:
            需要保护的消息索引集合
        """
        protected = set()
        
        for qa_pair in qa_pairs:
            # 保护问答对本身
            protected.add(qa_pair.question_idx)
            protected.add(qa_pair.answer_idx)
            
            # 保护上下文窗口
            for offset in range(-window_size, window_size + 1):
                q_idx = qa_pair.question_idx + offset
                a_idx = qa_pair.answer_idx + offset
                
                if 0 <= q_idx < len(messages):
                    protected.add(q_idx)
                if 0 <= a_idx < len(messages):
                    protected.add(a_idx)
        
        return protected

    async def _extract_dialog_important(self, dialog_text: str) -> DialogExtractionResponse:
        """对话级一次性抽取：从整段对话中提取重要信息并判定相关性。

        改进版：
        - LRU缓存管理
        - 重试机制
        - 降级策略
        """
        # 缓存命中则直接返回（场景+内容作为键）
        cache_key = f"{self.config.pruning_scene}:" + hashlib.sha1(dialog_text.encode("utf-8")).hexdigest()
        
        # LRU缓存：如果命中，移到末尾（最近使用）
        if cache_key in self._dialog_extract_cache:
            self._dialog_extract_cache.move_to_end(cache_key)
            return self._dialog_extract_cache[cache_key]

        # LRU缓存大小限制：超过限制时删除最旧的条目
        if len(self._dialog_extract_cache) >= self._cache_max_size:
            # 删除最旧的条目（OrderedDict的第一个）
            oldest_key = next(iter(self._dialog_extract_cache))
            del self._dialog_extract_cache[oldest_key]
            self._log(f"[剪枝-缓存] LRU缓存已满，删除最旧条目")

        rendered = self.template.render(
            pruning_scene=self.config.pruning_scene,
            ontology_class_infos=self._ontology_class_infos,
            dialog_text=dialog_text,
            language=self.language
        )
        log_template_rendering("extracat_Pruning.jinja2", {
            "pruning_scene": self.config.pruning_scene,
            "ontology_class_infos_count": len(self._ontology_class_infos),
            "language": self.language
        })
        log_prompt_rendering("pruning-extract", rendered)

        # 强制使用 LLM
        if not self.llm_client:
            raise RuntimeError("llm_client 未配置；请配置 LLM 以进行结构化抽取。")

        messages = [
            {"role": "system", "content": "你是一个严谨的场景抽取助手，只输出严格 JSON。"},
            {"role": "user", "content": rendered},
        ]
        
        # 重试机制
        max_retries = 3
        for attempt in range(max_retries):
            try:
                ex = await self.llm_client.response_structured(messages, DialogExtractionResponse)
                self._dialog_extract_cache[cache_key] = ex
                return ex
            except Exception as e:
                if attempt < max_retries - 1:
                    self._log(f"[剪枝-LLM] 第 {attempt + 1} 次尝试失败，重试中... 错误: {str(e)[:100]}")
                    await asyncio.sleep(0.5 * (attempt + 1))  # 指数退避
                    continue
                else:
                    # 降级策略：标记为相关，避免误删
                    self._log(f"[剪枝-LLM] LLM 调用失败 {max_retries} 次，使用降级策略（标记为相关）")
                    fallback_response = DialogExtractionResponse(
                        is_related=True,
                        times=[],
                        ids=[],
                        amounts=[],
                        contacts=[],
                        addresses=[],
                        keywords=[]
                    )
                    return fallback_response

    def _get_pruning_mode(self) -> str:
        """根据 pruning_threshold 返回当前剪枝阶段。

        - 低阈值 [0.0, 0.3)：conservative  只删填充，保留所有实质内容
        - 中阈值 [0.3, 0.6)：semantic      保留场景相关 + 有语义关联的内容，删除无关联内容
        - 高阈值 [0.6, 0.9]：strict        只保留场景相关内容，跨场景内容可被删除
        """
        t = float(self.config.pruning_threshold)
        if t < 0.3:
            return "conservative"
        elif t < 0.6:
            return "semantic"
        else:
            return "strict"

    def _apply_related_dialog_pruning(
        self,
        msgs: List[ConversationMessage],
        extraction: "DialogExtractionResponse",
        dialog_label: str,
        pruning_mode: str,
    ) -> List[ConversationMessage]:
        """相关对话统一剪枝入口，消除 prune_dialog / prune_dataset 中的重复逻辑。

        - conservative：只删填充
        - semantic / strict：场景感知剪枝
        """
        if pruning_mode == "conservative":
            preserve_tokens = self._build_preserve_tokens(extraction)
            return self._prune_fillers_only(msgs, preserve_tokens, dialog_label)
        else:
            return self._prune_with_scene_filter(msgs, extraction, dialog_label, pruning_mode)

    def _prune_fillers_only(
        self,
        msgs: List[ConversationMessage],
        preserve_tokens: List[str],
        dialog_label: str,
    ) -> List[ConversationMessage]:
        """相关对话专用：只删填充消息，LLM 保护消息和实质内容一律保留。

        不受 pruning_threshold 约束，删多少算多少（填充有多少删多少）。
        至少保留 1 条消息。
        注意：填充检测优先于 preserve_tokens 保护——填充消息本身无信息价值，
        即使 LLM 误将其关键词放入 preserve_tokens 也应删除。
        """
        to_delete_ids: set = set()
        for m in msgs:
            # 最高优先级保护：带有文件的消息一律保留，不参与任何剪枝判断
            if message_has_files(m):
                self._log(f"  [保护] 带文件的消息（不参与剪枝）：'{m.msg[:40]}'，文件数={len(m.files)}")
                continue
                
            # 填充检测优先：先判断是否为填充，再看 LLM 保护
            if self._is_filler_message(m):
                to_delete_ids.add(id(m))
                self._log(f"  [填充] '{m.msg[:40]}' → 删除")
                continue
            if self._msg_matches_tokens(m, preserve_tokens):
                self._log(f"  [保护] '{m.msg[:40]}' → LLM保护，跳过")

        kept = [m for m in msgs if id(m) not in to_delete_ids]
        if not kept and msgs:
            kept = [msgs[0]]

        deleted = len(msgs) - len(kept)
        self._log(
            f"[剪枝-相关] {dialog_label} 总消息={len(msgs)} "
            f"填充删除={deleted} 保留={len(kept)}"
        )
        return kept

    def _prune_with_scene_filter(
        self,
        msgs: List[ConversationMessage],
        extraction: "DialogExtractionResponse",
        dialog_label: str,
        mode: str,
    ) -> List[ConversationMessage]:
        """场景感知剪枝，供 semantic / strict 两个阈值档位调用。

        本函数体现剪枝系统的三层递进逻辑：

        第一层（conservative，阈值 < 0.3）：
            不进入本函数，由 _prune_fillers_only 处理。
            保留标准：只问"有没有信息量"，填充消息（嗯/好的/哈哈等）删除，其余一律保留。

        第二层（semantic，阈值 [0.3, 0.6)）：
            保留标准：内容价值优先，场景相关性是参考而非唯一标准。
            - 填充消息 → 删除（最高优先级）
            - 场景相关消息 → 保留
            - 场景无关消息 → 有两次豁免机会：
                1. 命中 scene_preserve_tokens（LLM 标记的关键词/时间/金额等）→ 保留
                2. 含情感词（感觉/压力/开心等）→ 保留（情感内容有记忆价值）
                3. 两次豁免均未命中 → 删除

        第三层（strict，阈值 [0.6, 0.9]）：
            保留标准：场景相关性优先，无任何豁免。
            - 填充消息 → 删除（最高优先级）
            - 场景相关消息 → 保留
            - 场景无关消息 → 直接删除，preserve_keywords 和情感词在此模式下均不生效

        至少保留 1 条消息（兜底取第一条）。
        """
        # strict 模式收窄保护范围：只保护结构化关键信息（时间/编号/金额/联系方式/地址），
        # 不保护 keywords / preserve_keywords，让场景过滤能删掉更多内容。
        # semantic 模式完整保护：包含 LLM 抽取的所有重要片段（含 keywords 和 preserve_keywords）。
        if mode == "strict":
            scene_preserve_tokens = (
                extraction.times + extraction.ids + extraction.amounts +
                extraction.contacts + extraction.addresses
            )
        else:
            scene_preserve_tokens = self._build_preserve_tokens(extraction)

        unrelated_snippets = extraction.scene_unrelated_snippets or []

        to_delete_ids: set = set()
        for m in msgs:
            msg_text = m.msg.strip()
            
            # 最高优先级保护：带有文件的消息一律保留，不参与任何剪枝判断
            if message_has_files(m):
                self._log(f"  [保护] 带文件的消息（不参与剪枝）：'{msg_text[:40]}'，文件数={len(m.files)}")
                continue

            # 第一优先级：填充消息无论模式直接删除，不参与后续场景判断
            if self._is_filler_message(m):
                to_delete_ids.add(id(m))
                self._log(f"  [填充] '{msg_text[:40]}' → 删除")
                continue

            # 双向包含匹配：处理 LLM 返回片段与原始消息文本长度不完全一致的情况
            is_scene_unrelated = any(
                snip and (snip in msg_text or msg_text in snip)
                for snip in unrelated_snippets
            )

            if is_scene_unrelated:
                if mode == "strict":
                    # strict：场景无关直接删除，不做任何豁免
                    # 场景相关性是唯一裁决标准，preserve_keywords 在此模式下不生效
                    to_delete_ids.add(id(m))
                    self._log(f"  [场景无关-严格] '{msg_text[:40]}' → 删除")
                elif mode == "semantic":
                    # semantic：场景无关但有内容价值 → 保留
                    # 豁免第一层：命中 scene_preserve_tokens（关键词/结构化信息保护）
                    if self._msg_matches_tokens(m, scene_preserve_tokens):
                        self._log(f"  [保护] '{msg_text[:40]}' → 场景关键词保护，保留")
                    else:
                        # 豁免第二层：含情感词，认为有情境记忆价值，即使场景无关也保留
                        has_contextual_emotion = any(
                            word in msg_text
                            for word in ["感觉", "觉得", "心情", "开心", "难过", "高兴", "沮丧",
                                         "喜欢", "讨厌", "爱", "恨", "担心", "害怕", "兴奋",
                                         "压力", "累", "疲惫", "烦", "焦虑", "委屈", "感动"]
                        )
                        if not has_contextual_emotion:
                            to_delete_ids.add(id(m))
                            self._log(f"  [场景无关-语义] '{msg_text[:40]}' → 删除（无情感关联）")
                        else:
                            self._log(f"  [场景关联-保留] '{msg_text[:40]}' → 有情感关联，保留")
            else:
                # 不在 scene_unrelated_snippets 中 → 场景相关，直接保留
                if self._msg_matches_tokens(m, scene_preserve_tokens):
                    self._log(f"  [保护] '{msg_text[:40]}' → LLM保护，跳过")
                # else: 普通场景相关消息，保留，不输出日志

        kept = [m for m in msgs if id(m) not in to_delete_ids]
        if not kept and msgs:
            kept = [msgs[0]]

        deleted = len(msgs) - len(kept)
        self._log(
            f"[剪枝-{mode}] {dialog_label} 总消息={len(msgs)} "
            f"删除={deleted} 保留={len(kept)}"
        )
        return kept

    def _build_preserve_tokens(self, extraction: "DialogExtractionResponse") -> List[str]:
        """统一构建 preserve_tokens，合并 LLM 抽取的所有重要片段。"""
        return (
            extraction.times + extraction.ids + extraction.amounts +
            extraction.contacts + extraction.addresses + extraction.keywords +
            extraction.preserve_keywords
        )

    def _msg_matches_tokens(self, message: ConversationMessage, tokens: List[str]) -> bool:
        """判断消息是否包含任意抽取到的重要片段。"""
        if not tokens:
            return False
        t = message.msg
        return any(tok and (tok in t) for tok in tokens)

    async def prune_dialog(self, dialog: DialogData) -> DialogData:
        """单对话剪枝：使用一次性对话抽取，避免逐条消息 LLM 调用。

        流程：
        - 对整段对话进行抽取与相关性判定；若相关则不剪；
        - 若不相关：用抽取到的重要片段 + 简单启发识别重要消息，按比例删除不相关消息，优先删除不重要，再删除重要（但重要最多按比例）。
        - 删除策略：不重要消息按出现顺序删除（确定性、无随机）。
        """
        if not self.config.pruning_switch:
            return dialog

        proportion = float(self.config.pruning_threshold)
        extraction = await self._extract_dialog_important(dialog.content)
        pruning_mode = self._get_pruning_mode()
        self._log(f"[剪枝-模式] 阈值={proportion} → 模式={pruning_mode}")

        if extraction.is_related:
            kept = self._apply_related_dialog_pruning(
                dialog.context.msgs, extraction, f"对话ID={dialog.id}", pruning_mode
            )
            dialog.context = ConversationContext(msgs=kept)
            return dialog

        # 在不相关对话中，LLM 已通过 preserve_tokens 标记需要保护的内容
        preserve_tokens = self._build_preserve_tokens(extraction)
        msgs = dialog.context.msgs

        # 分类：填充 / 其他可删（LLM保护消息通过不加入任何桶来隐式保护）
        filler_ids: set = set()
        deletable: List[ConversationMessage] = []

        for m in msgs:
            if self._msg_matches_tokens(m, preserve_tokens):
                pass  # 保护消息：不加入任何桶，不会被删除
            elif self._is_filler_message(m):
                filler_ids.add(id(m))
            else:
                deletable.append(m)

        # 计算删除目标
        total_unrel = len(msgs)
        delete_target = int(total_unrel * proportion)
        if proportion > 0 and total_unrel > 0 and delete_target == 0:
            delete_target = 1
        max_deletable = min(len(filler_ids) + len(deletable), max(0, total_unrel - 1))
        delete_target = min(delete_target, max_deletable)

        # 优先删填充，再删其他可删消息（按出现顺序）
        to_delete_ids: set = set()
        for m in msgs:
            if len(to_delete_ids) >= delete_target:
                break
            if id(m) in filler_ids:
                to_delete_ids.add(id(m))
        for m in deletable:
            if len(to_delete_ids) >= delete_target:
                break
            to_delete_ids.add(id(m))

        kept_msgs = [m for m in msgs if id(m) not in to_delete_ids]
        if not kept_msgs and msgs:
            kept_msgs = [msgs[0]]

        deleted_total = len(msgs) - len(kept_msgs)
        protected_count = len(msgs) - len(filler_ids) - len(deletable)
        self._log(
            f"[剪枝-对话] 对话ID={dialog.id} 总消息={len(msgs)} "
            f"(保护={protected_count} 填充={len(filler_ids)} 可删={len(deletable)}) "
            f"删除目标={delete_target} 实删={deleted_total} 保留={len(kept_msgs)}"
        )

        dialog.context = ConversationContext(msgs=kept_msgs)
        return dialog

    async def prune_dataset(self, dialogs: List[DialogData]) -> List[DialogData]:
        """数据集层面：全局消息级剪枝，保留所有对话。

        改进版：
        - 消息级独立判断，每条消息根据场景规则独立评估
        - 问答对保护已注释（暂不启用，留作观察）
        - 优化删除策略：填充消息 → 不重要消息 → 低分重要消息
        - 只删除"不重要的不相关消息"，重要信息（时间、编号等）强制保留
        - 保证每段对话至少保留1条消息，不会删除整段对话
        """
        # 如果剪枝功能关闭，直接返回原始数据集
        if not self.config.pruning_switch:
            return dialogs

        # 阈值保护：最高0.9
        proportion = float(self.config.pruning_threshold)
        if proportion > 0.9:
            logger.warning(f"[剪枝-数据集] 阈值{proportion}超过上限0.9，已自动调整为0.9")
            proportion = 0.9
        if proportion < 0.0:
            proportion = 0.0

        self._log(
            f"[剪枝-数据集] 对话总数={len(dialogs)} 场景={self.config.pruning_scene} 删除比例={proportion} 开关={self.config.pruning_switch} 模式=消息级独立判断"
        )

        pruning_mode = self._get_pruning_mode()
        self._log(f"[剪枝-数据集] 阈值={proportion} → 剪枝阶段={pruning_mode}")

        result: List[DialogData] = []
        total_original_msgs = 0
        total_deleted_msgs = 0

        # 统计对象：直接收集结构化数据，无需事后正则解析
        stats = {
            "scene": self.config.pruning_scene,
            "dialog_total": len(dialogs),
            "deletion_ratio": proportion,
            "enabled": self.config.pruning_switch,
            "pruning_mode": pruning_mode,
            "related_count": 0,
            "unrelated_count": 0,
            "related_indices": [],
            "unrelated_indices": [],
            "total_deleted_messages": 0,
            "remaining_dialogs": 0,
            "dialogs": [],
        }

        # 并发执行所有对话的 LLM 抽取（获取 preserve_keywords 等保护信息）
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def extract_with_semaphore(dd: DialogData) -> DialogExtractionResponse:
            async with semaphore:
                try:
                    return await self._extract_dialog_important(dd.content)
                except Exception as e:
                    self._log(f"[剪枝-LLM] 对话抽取失败，使用降级策略: {str(e)[:100]}")
                    return DialogExtractionResponse(is_related=True)

        extraction_tasks = [extract_with_semaphore(dd) for dd in dialogs]
        extraction_results: List[DialogExtractionResponse] = await asyncio.gather(*extraction_tasks)

        for d_idx, (dd, extraction) in enumerate(zip(dialogs, extraction_results)):
            msgs = dd.context.msgs
            original_count = len(msgs)
            total_original_msgs += original_count

            # 相关对话：根据阶段决定处理力度
            if extraction.is_related:
                stats["related_count"] += 1
                stats["related_indices"].append(d_idx + 1)
                kept = self._apply_related_dialog_pruning(
                    msgs, extraction, f"对话 {d_idx+1}", pruning_mode
                )
                deleted_count = original_count - len(kept)
                total_deleted_msgs += deleted_count
                dd.context.msgs = kept
                result.append(dd)
                stats["dialogs"].append({
                    "index": d_idx + 1,
                    "is_related": True,
                    "total_messages": original_count,
                    "deleted": deleted_count,
                    "kept": len(kept),
                })
                continue

            stats["unrelated_count"] += 1
            stats["unrelated_indices"].append(d_idx + 1)

            # 从 LLM 抽取结果中获取所有需要保留的 token
            preserve_tokens = self._build_preserve_tokens(extraction)

            # 判断是否需要详细日志
            should_log_details = self._detailed_prune_logging and original_count <= self._max_debug_msgs_per_dialog
            if self._detailed_prune_logging and original_count > self._max_debug_msgs_per_dialog:
                self._log(f"  对话[{d_idx}]消息数={original_count}，仅采样前{self._max_debug_msgs_per_dialog}条进行详细日志")

            if extraction.preserve_keywords:
                self._log(f"  对话[{d_idx}] LLM抽取到情绪/兴趣保护词: {extraction.preserve_keywords}")

            # 消息级分类：LLM保护 / 填充 / 其他可删
            llm_protected_msgs = []  # LLM 保护消息（preserve_tokens 命中）：绝对不可删除
            filler_msgs = []         # 填充消息（优先删除）
            deletable_msgs = []      # 其余消息（按比例删除）

            for idx, m in enumerate(msgs):
                msg_text = m.msg.strip()
                
                # 最高优先级保护：带有文件的消息一律保留，不参与分类
                if message_has_files(m):
                    self._log(f"  [保护] 带文件的消息（不参与分类，直接保留）：索引{idx}, '{msg_text[:40]}', 文件数={len(m.files)}")
                    llm_protected_msgs.append((idx, m))  # 放入保护列表
                    continue

                if self._msg_matches_tokens(m, preserve_tokens):
                    llm_protected_msgs.append((idx, m))
                    if should_log_details or idx < self._max_debug_msgs_per_dialog:
                        self._log(f"  [{idx}] '{msg_text[:30]}...' → 保护（LLM，不可删）")
                elif self._is_filler_message(m):
                    filler_msgs.append((idx, m))
                    if should_log_details or idx < self._max_debug_msgs_per_dialog:
                        self._log(f"  [{idx}] '{msg_text[:30]}...' → 填充")
                else:
                    deletable_msgs.append((idx, m))
                    if should_log_details or idx < self._max_debug_msgs_per_dialog:
                        self._log(f"  [{idx}] '{msg_text[:30]}...' → 可删")

            # important_msgs 仅用于日志统计
            important_msgs = llm_protected_msgs

            # 计算删除配额
            delete_target = int(original_count * proportion)
            if proportion > 0 and original_count > 0 and delete_target == 0:
                delete_target = 1

            # 确保至少保留1条消息
            max_deletable = max(0, original_count - 1)
            delete_target = min(delete_target, max_deletable)

            # 删除策略：优先删填充消息，再按出现顺序删其余可删消息
            to_delete_indices = set()
            deleted_details = []

            # 第一步：删除填充消息
            for idx, msg in filler_msgs:
                if len(to_delete_indices) >= delete_target:
                    break
                to_delete_indices.add(idx)
                deleted_details.append(f"[{idx}] 填充: '{msg.msg[:50]}'")

            # 第二步：如果还需要删除，按出现顺序删可删消息
            for idx, msg in deletable_msgs:
                if len(to_delete_indices) >= delete_target:
                    break
                to_delete_indices.add(idx)
                deleted_details.append(f"[{idx}] 可删: '{msg.msg[:50]}'")

            # 执行删除
            kept_msgs = []
            for idx, m in enumerate(msgs):
                if idx not in to_delete_indices:
                    kept_msgs.append(m)

            # 确保至少保留1条
            if not kept_msgs and msgs:
                kept_msgs = [msgs[0]]

            dd.context.msgs = kept_msgs
            deleted_count = original_count - len(kept_msgs)
            total_deleted_msgs += deleted_count

            # 输出删除详情
            if deleted_details:
                self._log(f"[剪枝-删除详情] 对话 {d_idx+1} 删除了以下消息:")
                for detail in deleted_details:
                    self._log(f"  {detail}")

            # ========== 问答对统计（已注释） ==========
            # qa_info = f"，问答对={len(qa_pairs)}" if qa_pairs else ""
            # ========================================

            self._log(
                f"[剪枝-对话] 对话 {d_idx+1} 总消息={original_count} "
                f"(保护={len(important_msgs)} 填充={len(filler_msgs)} 可删={len(deletable_msgs)}) "
                f"删除={deleted_count} 保留={len(kept_msgs)}"
            )

            stats["dialogs"].append({
                "index": d_idx + 1,
                "is_related": False,
                "total_messages": original_count,
                "protected": len(important_msgs),
                "fillers": len(filler_msgs),
                "deletable": len(deletable_msgs),
                "deleted": deleted_count,
                "kept": len(kept_msgs),
            })

            result.append(dd)

        # 补全统计对象
        stats["total_deleted_messages"] = total_deleted_msgs
        stats["remaining_dialogs"] = len(result)

        self._log(f"[剪枝-数据集] 剩余对话数={len(result)}")
        self._log(f"[剪枝-数据集] 相关对话数={stats['related_count']} 不相关对话数={stats['unrelated_count']}")
        self._log(f"[剪枝-数据集] 总删除 {total_deleted_msgs} 条")

        # 直接序列化统计对象，无需正则解析
        try:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            log_output_path = settings.get_memory_output_path("pruned_terminal.json")
            with open(log_output_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"[剪枝-数据集] 保存终端输出日志失败：{e}")

        # Safety: avoid empty dataset
        if not result:
            logger.warning("语义剪枝后数据集为空，已回退为未剪枝数据以避免流程中断")
            return dialogs

        return result

    def _log(self, msg: str) -> None:
        """记录日志并打印到终端。"""
        try:
            self.run_logs.append(msg)
        except Exception:
            pass
        logger.debug(msg)


