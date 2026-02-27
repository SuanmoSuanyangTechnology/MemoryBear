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
        
        # Load Jinja2 template
        self.template = prompt_env.get_template("extracat_Pruning.jinja2")
        
        # 对话抽取缓存：使用 OrderedDict 实现 LRU 缓存
        self._dialog_extract_cache: OrderedDict[str, DialogExtractionResponse] = OrderedDict()
        self._cache_max_size = 1000  # 缓存大小限制
        
        # 运行日志：收集关键终端输出，便于写入 JSON
        self.run_logs: List[str] = []
        
        # 扩展的填充词库（包含表情符号和网络用语）
        self._extended_fillers = [
            # 基础寒暄
            "你好", "您好", "在吗", "在的", "在呢", "嗯", "嗯嗯", "哦", "哦哦",
            "好的", "好", "行", "可以", "不可以", "谢谢", "多谢", "感谢",
            "拜拜", "再见", "88", "拜", "回见",
            # 口头禅
            "哈哈", "呵呵", "哈哈哈", "嘿嘿", "嘻嘻", "hiahia",
            "额", "呃", "啊", "诶", "唉", "哎", "嗯哼",
            # 确认词
            "是的", "对", "对的", "没错", "嗯嗯", "好嘞", "收到", "明白", "了解", "知道了",
            # 标点和符号
            "。。。", "...", "???", "？？？", "!!!", "！！！",
            # 表情符号（文本形式）
            "[微笑]", "[呲牙]", "[发呆]", "[得意]", "[流泪]", "[害羞]", "[闭嘴]",
            "[睡]", "[大哭]", "[尴尬]", "[发怒]", "[调皮]", "[龇牙]", "[惊讶]",
            "[难过]", "[酷]", "[冷汗]", "[抓狂]", "[吐]", "[偷笑]", "[可爱]",
            "[白眼]", "[傲慢]", "[饥饿]", "[困]", "[惊恐]", "[流汗]", "[憨笑]",
            # 网络用语
            "hhh", "hhhh", "2333", "666", "gg", "ok", "OK", "okok",
            "emmm", "emm", "em", "mmp", "wtf", "omg",
        ]

    def _is_important_message(self, message: ConversationMessage) -> bool:
        """基于启发式规则识别重要信息消息，优先保留。

        改进版：增强了规则覆盖范围，包括：
        - 含日期/时间（如YYYY-MM-DD、HH:MM、2024年11月10日、上午/下午）
        - 含编号/ID/订单号/申请号/账号/电话/金额等关键字段
        - 关键词："时间"、"日期"、"编号"、"订单"、"流水"、"金额"、"￥"、"元"、"电话"、"手机号"、"邮箱"、"地址"
        - 新增：问句识别、决策性语句、承诺性语句
        """
        text = message.msg.strip()
        if not text:
            return False
        
        patterns = [
            # 原有模式
            r"\d{4}-\d{1,2}-\d{1,2}",  # 修复：移除 \b 边界，因为中文前后没有单词边界
            r"\d{1,2}:\d{2}",  # 修复：移除 \b
            r"\d{4}年\d{1,2}月\d{1,2}日",
            r"上午|下午|AM|PM|今天|明天|后天|昨天|前天|本周|下周|上周|本月|下月|上月",
            r"订单号|工单|申请号|编号|ID|账号|账户|流水号|单号",
            r"电话|手机号|微信|QQ|邮箱|联系方式",
            r"地址|地点|位置|门牌号",
            r"金额|费用|价格|¥|￥|\d+元|人民币|美元|欧元",
            r"时间|日期|有效期|截止|期限|到期",
            # 新增模式
            r"什么|为什么|怎么|如何|哪里|哪个|谁|多少|几点|何时",  # 问句关键词
            r"必须|一定|务必|需要|要求|规定|应该",  # 决策性语句
            r"承诺|保证|确保|负责|同意|答应",  # 承诺性语句
            r"\d{11}",  # 11位手机号
            r"\d{3,4}-\d{7,8}",  # 固定电话
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",  # 邮箱
        ]
        
        for p in patterns:
            if re.search(p, text, flags=re.IGNORECASE):
                return True
        
        # 检查是否为问句（以问号结尾或包含疑问词）
        if text.endswith("？") or text.endswith("?"):
            return True
            
        return False
    
    def _importance_score(self, message: ConversationMessage) -> int:
        """为重要消息打分，用于在保留比例内优先保留更关键的内容。

        改进版：更细致的评分体系（0-10分）
        """
        text = message.msg.strip()
        score = 0
        
        weights = [
            # 高优先级（4-5分）
            (r"订单号|工单|申请号|编号|ID|账号|账户", 5),
            (r"金额|费用|价格|¥|￥|\d+元", 5),
            (r"\d{11}", 4),  # 手机号
            (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", 4),  # 邮箱
            
            # 中优先级（2-3分）
            (r"\d{4}-\d{1,2}-\d{1,2}", 3),  # 修复：移除 \b
            (r"\d{4}年\d{1,2}月\d{1,2}日", 3),
            (r"电话|手机号|微信|QQ|联系方式", 3),
            (r"地址|地点|位置", 2),
            (r"时间|日期|有效期|截止|明天|后天|下周|下月", 2),  # 新增时间相关词
            
            # 低优先级（1分）
            (r"\d{1,2}:\d{2}", 1),  # 修复：移除 \b
            (r"上午|下午|AM|PM", 1),
        ]
        
        for p, w in weights:
            if re.search(p, text, flags=re.IGNORECASE):
                score += w
        
        # 问句加分
        if text.endswith("？") or text.endswith("?"):
            score += 2
        
        # 长度加分（较长的消息通常包含更多信息）
        if len(text) > 50:
            score += 1
        if len(text) > 100:
            score += 1
            
        return min(score, 10)  # 最高10分

    def _is_filler_message(self, message: ConversationMessage) -> bool:
        """检测典型寒暄/口头禅/确认类短消息，用于跳过LLM分类以加速。

        改进版：扩展了填充词库，支持表情符号和网络用语
        满足以下之一视为填充消息：
        - 纯标点或长度很短（<= 4 个汉字或 <= 8 个字符）且不包含数字或关键实体
        - 在扩展填充词库中
        - 纯表情符号
        """
        t = message.msg.strip()
        if not t:
            return True
        
        # 检查是否在扩展填充词库中
        if t in self._extended_fillers:
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
        
        # 长度与字符类型判断
        if len(t) <= 8:
            # 非数字、无关键实体的短文本
            if not re.search(r"[0-9]", t) and not self._is_important_message(message):
                # 主要是标点或简单确认词
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
        
        Args:
            messages: 消息列表
            
        Returns:
            问答对列表
        """
        qa_pairs = []
        
        for i in range(len(messages) - 1):
            current_msg = messages[i].msg.strip()
            next_msg = messages[i + 1].msg.strip()
            
            # 简单规则：如果当前消息是问句，下一条消息可能是答案
            is_question = (
                current_msg.endswith("？") or 
                current_msg.endswith("?") or
                any(word in current_msg for word in ["什么", "为什么", "怎么", "如何", "哪里", "哪个", "谁", "多少", "几点", "何时", "吗"])
            )
            
            if is_question and next_msg:
                # 检查下一条消息是否像答案（不是另一个问句）
                is_answer = not (next_msg.endswith("？") or next_msg.endswith("?"))
                
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
            dialog_text=dialog_text,
            language=self.language
        )
        log_template_rendering("extracat_Pruning.jinja2", {
            "pruning_scene": self.config.pruning_scene,
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
        - 并发处理对话级相关性判断
        - 问答对识别和保护
        - 优化删除策略，保持上下文连贯性
        - 仅在"不相关对话"的范围内执行消息剪枝；相关对话不动
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
            f"[剪枝-数据集] 对话总数={len(dialogs)} 场景={self.config.pruning_scene} 删除比例={proportion} 开关={self.config.pruning_switch}"
        )
        
        # 并发处理对话级相关性分类
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def classify_dialog(idx: int, dd: DialogData):
            async with semaphore:
                try:
                    ex = await self._extract_dialog_important(dd.content)
                    return {
                        "dialog": dd,
                        "is_related": bool(ex.is_related),
                        "index": idx,
                        "extraction": ex
                    }
                except Exception as e:
                    self._log(f"[剪枝-并发] 对话 {idx} 分类失败: {str(e)[:100]}")
                    return {
                        "dialog": dd,
                        "is_related": True,  # 失败时标记为相关，避免误删
                        "index": idx,
                        "extraction": None
                    }
        
        # 并发执行所有对话的分类
        tasks = [classify_dialog(idx, dd) for idx, dd in enumerate(dialogs)]
        evaluated_dialogs = await asyncio.gather(*tasks)

        # 统计相关 / 不相关对话
        not_related_dialogs = [d for d in evaluated_dialogs if not d["is_related"]]
        related_dialogs = [d for d in evaluated_dialogs if d["is_related"]]
        self._log(
            f"[剪枝-数据集] 相关对话数={len(related_dialogs)} 不相关对话数={len(not_related_dialogs)}"
        )

        # 简洁打印第几段对话相关/不相关（索引基于1）
        def _fmt_indices(items, cap: int = 10):
            inds = [i["index"] + 1 for i in items]
            if len(inds) <= cap:
                return inds
            return inds[:cap] + ["...", f"共{len(inds)}个"]

        rel_inds = _fmt_indices(related_dialogs)
        nrel_inds = _fmt_indices(not_related_dialogs)
        self._log(f"[剪枝-数据集] 相关对话：第{rel_inds}段；不相关对话：第{nrel_inds}段")

        result: List[DialogData] = []
        if not_related_dialogs:
            # 为每个不相关对话进行分析
            per_dialog_info = {}
            total_unrelated = 0
            
            for d in not_related_dialogs:
                dd = d["dialog"]
                extraction = d.get("extraction")
                if extraction is None:
                    extraction = await self._extract_dialog_important(dd.content)
                
                # 合并所有重要标记
                tokens = extraction.times + extraction.ids + extraction.amounts + extraction.contacts + extraction.addresses + extraction.keywords
                msgs = dd.context.msgs
                
                # 识别问答对
                qa_pairs = self._identify_qa_pairs(msgs)
                protected_indices = self._get_protected_indices(msgs, qa_pairs, window_size=1)
                
                # 分类消息（考虑问答对保护）
                imp_unrel_msgs = []
                unimp_unrel_msgs = []
                
                for idx, m in enumerate(msgs):
                    # 问答对中的消息自动标记为重要
                    if idx in protected_indices:
                        imp_unrel_msgs.append((idx, m))
                    elif self._msg_matches_tokens(m, tokens) or self._is_important_message(m):
                        imp_unrel_msgs.append((idx, m))
                    elif not self._is_filler_message(m):
                        unimp_unrel_msgs.append((idx, m))
                    # 填充消息不加入任何列表，优先删除
                
                # 重要消息按重要性排序
                imp_sorted = sorted(imp_unrel_msgs, key=lambda x: self._importance_score(x[1]))
                imp_sorted_ids = [id(m) for _, m in imp_sorted]
                
                info = {
                    "dialog": dd,
                    "total_msgs": len(msgs),
                    "unrelated_count": len(msgs),
                    "imp_ids_sorted": imp_sorted_ids,
                    "unimp_ids": [id(m) for _, m in unimp_unrel_msgs],
                    "protected_indices": protected_indices,
                    "qa_pairs_count": len(qa_pairs),
                }
                per_dialog_info[d["index"]] = info
                total_unrelated += info["unrelated_count"]
            
            # 全局删除配额计算
            global_delete = int(total_unrelated * proportion)
            if proportion > 0 and total_unrelated > 0 and global_delete == 0:
                global_delete = 1
            
            # 每段的最大可删容量
            capacities = []
            for d in not_related_dialogs:
                idx = d["index"]
                info = per_dialog_info[idx]
                imp_count = len(info["imp_ids_sorted"])
                unimp_count = len(info["unimp_ids"])
                imp_cap = int(imp_count * proportion)
                cap = min(unimp_count + imp_cap, max(0, info["total_msgs"] - 1))
                capacities.append(cap)
            
            total_capacity = sum(capacities)
            if global_delete > total_capacity:
                self._log(f"[剪枝-数据集] 不相关消息总数={total_unrelated}，目标删除={global_delete}，最大可删={total_capacity}。将按最大可删执行。")
                global_delete = total_capacity

            # 配额分配
            alloc = []
            for i, d in enumerate(not_related_dialogs):
                idx = d["index"]
                info = per_dialog_info[idx]
                share = int(global_delete * (info["unrelated_count"] / total_unrelated)) if total_unrelated > 0 else 0
                alloc.append(min(share, capacities[i]))
            
            allocated = sum(alloc)
            rem = global_delete - allocated
            turn = 0
            while rem > 0 and turn < 100000:
                progressed = False
                for i in range(len(not_related_dialogs)):
                    if rem <= 0:
                        break
                    if alloc[i] < capacities[i]:
                        alloc[i] += 1
                        rem -= 1
                        progressed = True
                if not progressed:
                    break
                turn += 1

            # 应用删除
            total_deleted_confirm = 0
            for d in evaluated_dialogs:
                dd = d["dialog"]
                msgs = dd.context.msgs
                original = len(msgs)
                
                if d["is_related"]:
                    result.append(dd)
                    continue
                
                idx_in_unrel = next((k for k, x in enumerate(not_related_dialogs) if x["index"] == d["index"]), None)
                if idx_in_unrel is None:
                    result.append(dd)
                    continue
                
                quota = alloc[idx_in_unrel]
                info = per_dialog_info[d["index"]]
                
                # 计算删除ID
                imp_count = len(info["imp_ids_sorted"])
                imp_del_cap = int(imp_count * proportion)
                
                unimp_delete_ids = set(info["unimp_ids"][:min(quota, len(info["unimp_ids"]))])
                del_unimp = min(quota, len(unimp_delete_ids))
                rem_quota = quota - del_unimp
                
                imp_delete_ids = set(info["imp_ids_sorted"][:min(rem_quota, imp_del_cap)])
                
                deleted_here = 0
                actual_unimp_deleted = 0
                actual_imp_deleted = 0
                kept = []
                
                for m in msgs:
                    mid = id(m)
                    if mid in unimp_delete_ids and actual_unimp_deleted < del_unimp:
                        actual_unimp_deleted += 1
                        deleted_here += 1
                        continue
                    if mid in imp_delete_ids and actual_imp_deleted < len(imp_delete_ids):
                        actual_imp_deleted += 1
                        deleted_here += 1
                        continue
                    kept.append(m)
                
                if not kept and msgs:
                    kept = [msgs[0]]
                
                dd.context.msgs = kept
                total_deleted_confirm += deleted_here
                
                qa_info = f"，问答对={info['qa_pairs_count']}" if info['qa_pairs_count'] > 0 else ""
                self._log(
                    f"[剪枝-对话] 对话 {d['index']+1} 总消息={original} 分配删除={quota} 实删={deleted_here} 保留={len(kept)}{qa_info}"
                )
                result.append(dd)
            
            self._log(f"[剪枝-数据集] 全局消息级剪枝完成，总删除 {total_deleted_confirm} 条（保护问答对和上下文）。")
        else:
            result = [d["dialog"] for d in evaluated_dialogs]
        
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
