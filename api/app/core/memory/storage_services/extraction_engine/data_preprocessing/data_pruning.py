"""
语义剪枝器 - 在预处理与分块之间过滤与场景不相关内容

功能：
- 对话级一次性抽取判定相关性
- 仅对"不相关对话"的消息按比例删除
- 重要信息（时间、编号、金额、联系方式、地址等）优先保留
- 改进版：增强重要性判断、智能填充消息识别、问答对保护、并发优化
"""

import asyncio
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
from app.core.memory.utils.config.config_utils import get_pruning_config
from app.core.memory.utils.prompt.prompt_utils import prompt_env, log_prompt_rendering, log_template_rendering
from app.core.memory.storage_services.extraction_engine.data_preprocessing.scene_config import (
    SceneConfigRegistry,
    ScenePatterns
)


class DialogExtractionResponse(BaseModel):
    """对话级一次性抽取的结构化返回，用于加速剪枝。

    - is_related：对话与场景的相关性判定。
    - times / ids / amounts / contacts / addresses / keywords：重要信息片段，用来在不相关对话中保留关键消息。
    """
    is_related: bool = Field(...)
    times: List[str] = Field(default_factory=list)
    ids: List[str] = Field(default_factory=list)
    amounts: List[str] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    addresses: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


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
        
        # 加载场景特定配置（内置场景走专门规则，自定义场景 fallback 到通用规则）
        self.scene_config: ScenePatterns = SceneConfigRegistry.get_config(
            self.config.pruning_scene, 
            fallback_to_generic=True
        )
        
        # 判断是否为内置专门场景
        self._is_builtin_scene = SceneConfigRegistry.is_scene_supported(self.config.pruning_scene)
        
        # 自定义场景的本体类型列表（用于注入提示词）
        self._ontology_classes = getattr(self.config, "ontology_classes", None) or []
        
        if self._is_builtin_scene:
            self._log(f"[剪枝-初始化] 场景={self.config.pruning_scene} 使用内置专门配置")
        else:
            self._log(f"[剪枝-初始化] 场景={self.config.pruning_scene} 为自定义场景，使用通用规则 + 本体类型提示词注入")
            if self._ontology_classes:
                self._log(f"[剪枝-初始化] 注入本体类型: {self._ontology_classes}")
            else:
                self._log(f"[剪枝-初始化] 未找到本体类型，将使用通用提示词")
        
        # Load Jinja2 template
        self.template = prompt_env.get_template("extracat_Pruning.jinja2")
        
        # 对话抽取缓存：使用 OrderedDict 实现 LRU 缓存
        self._dialog_extract_cache: OrderedDict[str, DialogExtractionResponse] = OrderedDict()
        self._cache_max_size = 1000  # 缓存大小限制
        
        # 运行日志：收集关键终端输出，便于写入 JSON
        self.run_logs: List[str] = []

    def _is_important_message(self, message: ConversationMessage) -> bool:
        """基于启发式规则识别重要信息消息，优先保留。

        改进版：使用场景特定的模式进行识别
        - 根据 pruning_scene 动态加载对应的识别规则
        - 支持教育、在线服务、外呼三个场景的特定模式
        """
        text = message.msg.strip()
        if not text:
            return False
        
        # 使用场景特定的模式
        all_patterns = (
            self.scene_config.high_priority_patterns +
            self.scene_config.medium_priority_patterns +
            self.scene_config.low_priority_patterns
        )
        
        for pattern, _ in all_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        
        # 检查是否为问句（以问号结尾或包含疑问词）
        if text.endswith("？") or text.endswith("?"):
            return True
        
        # 检查是否包含问句关键词
        if any(keyword in text for keyword in self.scene_config.question_keywords):
            return True
        
        # 检查是否包含决策性关键词
        if any(keyword in text for keyword in self.scene_config.decision_keywords):
            return True
            
        return False
    
    def _importance_score(self, message: ConversationMessage) -> int:
        """为重要消息打分，用于在保留比例内优先保留更关键的内容。

        改进版：使用场景特定的权重体系（0-10分）
        - 根据场景动态调整不同信息类型的权重
        - 高优先级模式：4-6分
        - 中优先级模式：2-3分
        - 低优先级模式：1分
        """
        text = message.msg.strip()
        score = 0
        
        # 使用场景特定的权重
        for pattern, weight in self.scene_config.high_priority_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                score += weight
        
        for pattern, weight in self.scene_config.medium_priority_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                score += weight
        
        for pattern, weight in self.scene_config.low_priority_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                score += weight
        
        # 问句加分
        if text.endswith("？") or text.endswith("?"):
            score += 2
        
        # 包含问句关键词加分
        if any(keyword in text for keyword in self.scene_config.question_keywords):
            score += 1
        
        # 包含决策性关键词加分
        if any(keyword in text for keyword in self.scene_config.decision_keywords):
            score += 2
        
        # 长度加分（较长的消息通常包含更多信息）
        if len(text) > 50:
            score += 1
        if len(text) > 100:
            score += 1
            
        return min(score, 10)  # 最高10分

    def _is_filler_message(self, message: ConversationMessage) -> bool:
        """检测典型寒暄/口头禅/确认类短消息。

        改进版：更严格的填充消息判断，避免误删场景相关内容
        满足以下之一视为填充消息：
        - 纯标点或空白
        - 在场景特定填充词库中（精确匹配）
        - 纯表情符号
        - 常见寒暄（精确匹配短语）
        
        注意：不再使用长度判断，避免误删短但重要的消息
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
        
        # 检查是否为纯表情符号（方括号包裹）
        if re.fullmatch(r"(\[[^\]]+\])+", t):
            return True
        
        # 检查是否为纯emoji（Unicode表情）
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # 表情符号
            "\U0001F300-\U0001F5FF"  # 符号和象形文字
            "\U0001F680-\U0001F6FF"  # 交通和地图符号
            "\U0001F1E0-\U0001F1FF"  # 旗帜
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE
        )
        if emoji_pattern.fullmatch(t):
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
            is_builtin_scene=self._is_builtin_scene,
            ontology_classes=self._ontology_classes,
            dialog_text=dialog_text,
            language=self.language
        )
        log_template_rendering("extracat_Pruning.jinja2", {
            "pruning_scene": self.config.pruning_scene,
            "is_builtin_scene": self._is_builtin_scene,
            "ontology_classes_count": len(self._ontology_classes),
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
        if extraction.is_related:
            # 相关对话不剪枝
            return dialog

        # 在不相关对话中，识别重要/不重要消息
        tokens = extraction.times + extraction.ids + extraction.amounts + extraction.contacts + extraction.addresses + extraction.keywords
        msgs = dialog.context.msgs
        imp_unrel_msgs: List[ConversationMessage] = []
        unimp_unrel_msgs: List[ConversationMessage] = []
        for m in msgs:
            if self._msg_matches_tokens(m, tokens) or self._is_important_message(m):
                imp_unrel_msgs.append(m)
            else:
                unimp_unrel_msgs.append(m)
        # 计算总删除目标数量
        total_unrel = len(msgs)
        delete_target = int(total_unrel * proportion)
        if proportion > 0 and total_unrel > 0 and delete_target == 0:
            delete_target = 1
        imp_del_cap = min(int(len(imp_unrel_msgs) * proportion), len(imp_unrel_msgs))
        unimp_del_cap = len(unimp_unrel_msgs)
        max_capacity = max(0, len(msgs) - 1)
        max_deletable = min(imp_del_cap + unimp_del_cap, max_capacity)
        delete_target = min(delete_target, max_deletable)
        # 删除配额分配
        del_unimp = min(delete_target, unimp_del_cap)
        rem = delete_target - del_unimp
        del_imp = min(rem, imp_del_cap)

        # 选取删除集合
        unimp_delete_ids = []
        imp_delete_ids = []
        if del_unimp > 0:
            # 按出现顺序选取前 del_unimp 条不重要消息进行删除（确定性、可复现）
            unimp_delete_ids = [id(m) for m in unimp_unrel_msgs[:del_unimp]]
        if del_imp > 0:
            imp_sorted = sorted(imp_unrel_msgs, key=lambda m: self._importance_score(m))
            imp_delete_ids = [id(m) for m in imp_sorted[:del_imp]]

        # 统计实际删除数量（重要/不重要）
        actual_unimp_deleted = 0
        actual_imp_deleted = 0
        kept_msgs = []
        delete_targets = set(unimp_delete_ids) | set(imp_delete_ids)
        for m in msgs:
            mid = id(m)
            if mid in delete_targets:
                if mid in set(unimp_delete_ids) and actual_unimp_deleted < del_unimp:
                    actual_unimp_deleted += 1
                    continue
                if mid in set(imp_delete_ids) and actual_imp_deleted < del_imp:
                    actual_imp_deleted += 1
                    continue
            kept_msgs.append(m)
        if not kept_msgs and msgs:
            kept_msgs = [msgs[0]]

        deleted_total = actual_unimp_deleted + actual_imp_deleted
        self._log(
            f"[剪枝-对话] 对话ID={dialog.id} 总消息={len(msgs)} 删除目标={delete_target} 实删={deleted_total} 保留={len(kept_msgs)}"
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
            print(f"[剪枝-数据集] 阈值{proportion}超过上限0.9，已自动调整为0.9")
            proportion = 0.9
        if proportion < 0.0:
            proportion = 0.0

        self._log(
            f"[剪枝-数据集] 对话总数={len(dialogs)} 场景={self.config.pruning_scene} 删除比例={proportion} 开关={self.config.pruning_switch} 模式=消息级独立判断"
        )
        
        result: List[DialogData] = []
        total_original_msgs = 0
        total_deleted_msgs = 0
        
        for d_idx, dd in enumerate(dialogs):
            msgs = dd.context.msgs
            original_count = len(msgs)
            total_original_msgs += original_count
            
            # ========== 问答对保护（已注释，暂不启用，留作观察） ==========
            # qa_pairs = self._identify_qa_pairs(msgs)
            # protected_indices = self._get_protected_indices(msgs, qa_pairs, window_size=0)
            # ========================================================
            
            # 消息级分类：每条消息独立判断
            important_msgs = []  # 重要消息（保留）
            unimportant_msgs = []  # 不重要消息（可删除）
            filler_msgs = []  # 填充消息（优先删除）
            
            # 判断是否需要详细日志（仅对前N条消息记录）
            should_log_details = self._detailed_prune_logging and original_count <= self._max_debug_msgs_per_dialog
            if self._detailed_prune_logging and original_count > self._max_debug_msgs_per_dialog:
                self._log(f"  对话[{d_idx}]消息数={original_count}，仅采样前{self._max_debug_msgs_per_dialog}条进行详细日志")
            
            for idx, m in enumerate(msgs):
                msg_text = m.msg.strip()
                
                # ========== 问答对保护判断（已注释） ==========
                # if idx in protected_indices:
                #     important_msgs.append((idx, m))
                #     self._log(f"  [{idx}] '{msg_text[:30]}...' → 重要（问答对保护）")
                # ==========================================
                
                # 填充消息（寒暄、表情等）
                if self._is_filler_message(m):
                    filler_msgs.append((idx, m))
                    if should_log_details or idx < self._max_debug_msgs_per_dialog:
                        self._log(f"  [{idx}] '{msg_text[:30]}...' → 填充")
                # 重要信息（学号、成绩、时间、金额等）
                elif self._is_important_message(m):
                    important_msgs.append((idx, m))
                    if should_log_details or idx < self._max_debug_msgs_per_dialog:
                        self._log(f"  [{idx}] '{msg_text[:30]}...' → 重要（场景规则）")
                # 其他消息
                else:
                    unimportant_msgs.append((idx, m))
                    if should_log_details or idx < self._max_debug_msgs_per_dialog:
                        self._log(f"  [{idx}] '{msg_text[:30]}...' → 不重要")
            
            # 计算删除配额
            delete_target = int(original_count * proportion)
            if proportion > 0 and original_count > 0 and delete_target == 0:
                delete_target = 1
            
            # 确保至少保留1条消息
            max_deletable = max(0, original_count - 1)
            delete_target = min(delete_target, max_deletable)
            
            # 删除策略：优先删除填充消息，再删除不重要消息
            to_delete_indices = set()
            deleted_details = []  # 记录删除的消息详情
            
            # 第一步：删除填充消息
            filler_to_delete = min(len(filler_msgs), delete_target)
            for i in range(filler_to_delete):
                idx, msg = filler_msgs[i]
                to_delete_indices.add(idx)
                deleted_details.append(f"[{idx}] 填充: '{msg.msg[:50]}'")
            
            # 第二步：如果还需要删除，删除不重要消息
            remaining_quota = delete_target - len(to_delete_indices)
            if remaining_quota > 0:
                unimp_to_delete = min(len(unimportant_msgs), remaining_quota)
                for i in range(unimp_to_delete):
                    idx, msg = unimportant_msgs[i]
                    to_delete_indices.add(idx)
                    deleted_details.append(f"[{idx}] 不重要: '{msg.msg[:50]}'")
            
            # 第三步：如果还需要删除，按重要性分数删除重要消息
            remaining_quota = delete_target - len(to_delete_indices)
            if remaining_quota > 0 and important_msgs:
                # 按重要性分数排序（分数低的优先删除）
                imp_sorted = sorted(important_msgs, key=lambda x: self._importance_score(x[1]))
                imp_to_delete = min(len(imp_sorted), remaining_quota)
                for i in range(imp_to_delete):
                    idx, msg = imp_sorted[i]
                    to_delete_indices.add(idx)
                    score = self._importance_score(msg)
                    deleted_details.append(f"[{idx}] 重要(分数{score}): '{msg.msg[:50]}'")
            
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
                f"(重要={len(important_msgs)} 不重要={len(unimportant_msgs)} 填充={len(filler_msgs)}) "
                f"删除={deleted_count} 保留={len(kept_msgs)}"
            )
            
            result.append(dd)
        
        self._log(f"[剪枝-数据集] 剩余对话数={len(result)}")

        # 保存日志
        try:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            log_output_path = settings.get_memory_output_path("pruned_terminal.json")
            sanitized_logs = [self._sanitize_log_line(l) for l in self.run_logs]
            payload = self._parse_logs_to_structured(sanitized_logs)
            with open(log_output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"[剪枝-数据集] 保存终端输出日志失败：{e}")

        # Safety: avoid empty dataset
        if not result:
            print("警告: 语义剪枝后数据集为空，已回退为未剪枝数据以避免流程中断")
            return dialogs
        
        return result

    def _log(self, msg: str) -> None:
        """记录日志并打印到终端。"""
        try:
            self.run_logs.append(msg)
        except Exception:
            # 任何异常都不影响打印
            pass
        print(msg)

    def _sanitize_log_line(self, line: str) -> str:
        """移除行首的方括号标签前缀，例如 [剪枝-数据集] 或 [剪枝-对话]。"""
        try:
            return re.sub(r"^\[[^\]]+\]\s*", "", line)
        except Exception:
            return line

    def _parse_logs_to_structured(self, logs: List[str]) -> dict:
        """将已去前缀的日志列表解析为结构化 JSON，便于数据对接。"""
        summary = {
            "scene": self.config.pruning_scene,
            "dialog_total": None,
            "deletion_ratio": None,
            "enabled": None,
            "related_count": None,
            "unrelated_count": None,
            "related_indices": [],
            "unrelated_indices": [],
            "total_deleted_messages": None,
            "remaining_dialogs": None,
        }
        dialogs = []

        # 解析函数
        def parse_int(value: str) -> Optional[int]:
            try:
                return int(value)
            except Exception:
                return None

        def parse_float(value: str) -> Optional[float]:
            try:
                return float(value)
            except Exception:
                return None

        def parse_indices(s: str) -> List[int]:
            s = s.strip()
            if not s:
                return []
            parts = [p.strip() for p in s.split(",") if p.strip()]
            out: List[int] = []
            for p in parts:
                try:
                    out.append(int(p))
                except Exception:
                    pass
            return out

        # 正则
        re_header = re.compile(r"对话总数=(\d+)\s+场景=([^\s]+)\s+删除比例=([0-9.]+)\s+开关=(True|False)")
        re_counts = re.compile(r"相关对话数=(\d+)\s+不相关对话数=(\d+)")
        re_indices = re.compile(r"相关对话：第\[(.*?)\]段；不相关对话：第\[(.*?)\]段")
        re_dialog = re.compile(r"对话\s+(\d+)\s+总消息=(\d+)\s+分配删除=(\d+)\s+实删=(\d+)\s+保留=(\d+)")
        re_total_del = re.compile(r"总删除\s+(\d+)\s+条")
        re_remaining = re.compile(r"剩余对话数=(\d+)")

        for line in logs:
            # 第一行：总览
            m = re_header.search(line)
            if m:
                summary["dialog_total"] = parse_int(m.group(1))
                # 顶层 scene 依配置，这里不覆盖，但也可校验 m.group(2)
                summary["deletion_ratio"] = parse_float(m.group(3))
                summary["enabled"] = True if m.group(4) == "True" else False
                continue

            # 第二行：相关/不相关数量
            m = re_counts.search(line)
            if m:
                summary["related_count"] = parse_int(m.group(1))
                summary["unrelated_count"] = parse_int(m.group(2))
                continue

            # 第三行：相关/不相关索引
            m = re_indices.search(line)
            if m:
                summary["related_indices"] = parse_indices(m.group(1))
                summary["unrelated_indices"] = parse_indices(m.group(2))
                continue

            # 对话级统计
            m = re_dialog.search(line)
            if m:
                dialogs.append({
                    "index": parse_int(m.group(1)),
                    "total_messages": parse_int(m.group(2)),
                    "quota_delete": parse_int(m.group(3)),
                    "actual_deleted": parse_int(m.group(4)),
                    "kept": parse_int(m.group(5)),
                })
                continue

            # 全局删除总数
            m = re_total_del.search(line)
            if m:
                summary["total_deleted_messages"] = parse_int(m.group(1))
                continue

            # 剩余对话数
            m = re_remaining.search(line)
            if m:
                summary["remaining_dialogs"] = parse_int(m.group(1))
                continue

        return {
            "scene": summary["scene"],
            "timestamp": datetime.now().isoformat(),
            "summary": {k: v for k, v in summary.items() if k != "scene"},
            "dialogs": dialogs,
        }
