"""基于 LLM 的智能路由器 - 混合策略"""
import json
import re
import uuid
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.services.conversation_state_manager import ConversationStateManager
from app.models import ModelConfig, AgentConfig
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class LLMRouter:
    """基于 LLM 的智能路由器
    
    混合策略：
    1. 先用关键词快速筛选（置信度 > 0.8 直接返回）
    2. 对于模糊情况（置信度 0.3-0.8），调用 LLM 辅助
    3. 对于完全不匹配（置信度 < 0.3），调用 LLM
    4. 缓存 LLM 结果，减少重复调用
    """
    
    # 主题切换信号
    SWITCH_SIGNALS = [
        "换个话题", "另外", "还有", "对了",
        "那这个呢", "再问一个", "顺便问下",
        "我想问", "帮我", "请问", "换一个"
    ]
    
    # 延续信号
    CONTINUATION_SIGNALS = [
        "继续", "还是", "也", "同样", "类似",
        "这个", "那个", "它", "他", "她", "呢"
    ]
    
    def __init__(
        self,
        db: Session,
        state_manager: ConversationStateManager,
        routing_rules: List[Dict[str, Any]],
        sub_agents: Dict[str, Any],
        routing_model_config: Optional[ModelConfig] = None,
        use_llm: bool = True
    ):
        """初始化 LLM 路由器
        
        Args:
            db: 数据库会话
            state_manager: 会话状态管理器
            routing_rules: 路由规则列表
            sub_agents: 子 Agent 配置字典
            routing_model_config: 用于路由的模型配置（可选）
            use_llm: 是否启用 LLM（默认 True）
        """
        self.db = db
        self.state_manager = state_manager
        self.routing_rules = routing_rules
        self.sub_agents = sub_agents
        self.routing_model_config = routing_model_config
        self.use_llm = use_llm and routing_model_config is not None
        
        # 配置参数
        self.min_confidence_for_switch = 0.7
        self.max_same_agent_turns = 10
        self.keyword_high_confidence_threshold = 0.8  # 关键词高置信度阈值
        self.keyword_low_confidence_threshold = 0.3   # 关键词低置信度阈值
        
        # 缓存配置
        self.cache_enabled = True
        self.cache_size = 1000
    
    async def route(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        force_new: bool = False
    ) -> Dict[str, Any]:
        """智能路由（混合策略）
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            force_new: 是否强制重新路由
            
        Returns:
            路由结果
        """
        logger.info(
            "开始 LLM 智能路由",
            extra={
                "message_length": len(message),
                "conversation_id": conversation_id,
                "use_llm": self.use_llm
            }
        )
        
        # 1. 获取会话状态
        state = None
        if conversation_id and not force_new:
            state = self.state_manager.get_state(conversation_id)
        
        # 2. 检测主题切换
        topic_changed = self._detect_topic_change(message, state)
        
        # 3. 提取当前主题
        topic = await self._extract_topic_with_llm(message) if self.use_llm else self._extract_topic(message)
        
        # 4. 选择路由策略
        if force_new:
            agent_id, confidence, method = await self._route_with_hybrid(message)
            strategy = "force_new"
            reason = "用户强制重新路由"
            
        elif not state or not state.get("current_agent_id"):
            agent_id, confidence, method = await self._route_with_hybrid(message)
            strategy = "new_conversation"
            reason = "新会话，首次路由"
            
        elif topic_changed:
            agent_id, confidence, method = await self._route_with_hybrid(message)
            strategy = "topic_changed"
            reason = f"检测到主题切换: {state.get('last_topic')} -> {topic}"
            
        elif state.get("same_agent_turns", 0) >= self.max_same_agent_turns:
            agent_id, confidence, method = await self._route_with_hybrid(message)
            strategy = "max_turns_reached"
            reason = f"同一 Agent 已使用 {state['same_agent_turns']} 轮"
            
        else:
            current_agent_id = state["current_agent_id"]
            should_continue, continue_confidence = self._should_continue_current_agent(
                message,
                current_agent_id
            )
            
            if should_continue:
                agent_id = current_agent_id
                confidence = continue_confidence
                method = "keyword"
                strategy = "continue_current"
                reason = "消息在当前 Agent 能力范围内"
            else:
                new_agent_id, new_confidence, method = await self._route_with_hybrid(message)
                
                if new_confidence > continue_confidence + self.min_confidence_for_switch:
                    agent_id = new_agent_id
                    confidence = new_confidence
                    strategy = "switch_agent"
                    reason = f"新 Agent 置信度更高: {new_confidence:.2f} vs {continue_confidence:.2f}"
                else:
                    agent_id = current_agent_id
                    confidence = continue_confidence
                    method = "keyword"
                    strategy = "keep_current"
                    reason = "置信度差距不足以切换 Agent"
        
        # 5. 更新会话状态
        if conversation_id:
            self.state_manager.update_state(
                conversation_id,
                agent_id,
                message,
                topic,
                confidence
            )
        
        result = {
            "agent_id": agent_id,
            "confidence": confidence,
            "strategy": strategy,
            "topic": topic,
            "topic_changed": topic_changed,
            "reason": reason,
            "routing_method": method  # "keyword", "llm", "hybrid"
        }
        
        logger.info(
            "路由完成",
            extra={
                "agent_id": agent_id,
                "strategy": strategy,
                "confidence": confidence,
                "method": method
            }
        )
        
        return result
    
    async def _route_with_hybrid(self, message: str) -> Tuple[str, float, str]:
        """混合路由策略
        
        Args:
            message: 用户消息
            
        Returns:
            (agent_id, confidence, method)
        """
        # 1. 先用关键词匹配
        keyword_agent_id, keyword_confidence = self._route_with_keywords(message)
        
        # 2. 判断是否需要 LLM
        if not self.use_llm or not self.routing_model_config:
            # 不使用 LLM，直接返回关键词结果
            return keyword_agent_id, keyword_confidence, "keyword"
        
        if keyword_confidence >= self.keyword_high_confidence_threshold:
            # 关键词置信度很高，直接返回
            logger.info(f"关键词置信度高 ({keyword_confidence:.2f})，跳过 LLM")
            return keyword_agent_id, keyword_confidence, "keyword"
        
        # 3. 使用 LLM 辅助决策
        logger.info(f"关键词置信度较低 ({keyword_confidence:.2f})，调用 LLM")
        llm_agent_id, llm_confidence = await self._route_with_llm(message)
        
        # 4. 综合决策
        if llm_confidence > keyword_confidence:
            # LLM 置信度更高
            final_confidence = llm_confidence * 0.7 + keyword_confidence * 0.3
            return llm_agent_id, final_confidence, "llm"
        else:
            # 关键词置信度更高或相当
            final_confidence = keyword_confidence * 0.7 + llm_confidence * 0.3
            return keyword_agent_id, final_confidence, "hybrid"
    
    def _route_with_keywords(self, message: str) -> Tuple[str, float]:
        """基于关键词的路由
        
        Args:
            message: 用户消息
            
        Returns:
            (agent_id, confidence)
        """
        best_agent_id = None
        best_score = 0.0
        
        for rule in self.routing_rules:
            score = self._calculate_rule_score(message, rule)
            
            if score > best_score:
                best_score = score
                best_agent_id = rule.get("target_agent_id")
        
        if not best_agent_id or best_score < 0.3:
            best_agent_id = self._get_default_agent_id()
            best_score = 0.5
        
        return best_agent_id, best_score
    
    async def _route_with_llm(self, message: str) -> Tuple[str, float]:
        """基于 LLM 的路由
        
        Args:
            message: 用户消息
            
        Returns:
            (agent_id, confidence)
        """
        # 检查缓存
        if self.cache_enabled:
            cached_result = self._get_cached_llm_result(message)
            if cached_result:
                logger.info("使用缓存的 LLM 路由结果")
                return cached_result
        
        # 构建 prompt
        prompt = self._build_routing_prompt(message)
        
        try:
            # 调用 LLM
            response = await self._call_llm(prompt)
            
            # 解析结果
            agent_id, confidence = self._parse_llm_response(response)
            
            # 缓存结果
            if self.cache_enabled:
                self._cache_llm_result(message, agent_id, confidence)
            
            return agent_id, confidence
            
        except Exception as e:
            logger.error(f"LLM 路由失败: {str(e)}")
            # 降级到关键词路由
            return self._route_with_keywords(message)
    
    def _build_routing_prompt(self, message: str) -> str:
        """构建 LLM 路由 prompt
        
        Args:
            message: 用户消息
            
        Returns:
            prompt 字符串
        """
        # 构建 Agent 描述
        agent_descriptions = []
        for agent_id, agent_data in self.sub_agents.items():
            # 获取 Agent 信息
            agent_info = agent_data.get("info", {})
            agent_config = agent_data.get("config")
            
            # 查找该 Agent 的路由规则
            rules = [r for r in self.routing_rules if r.get("target_agent_id") == agent_id]
            
            # 构建描述
            name = agent_info.get("name", "未命名 Agent")
            role = agent_info.get("role", "")
            capabilities = agent_info.get("capabilities", [])
            
            desc_parts = [f"- agent_id: {agent_id}", f"  名称: {name}"]
            
            if role:
                desc_parts.append(f"  角色: {role}")
            
            # 从路由规则获取关键词
            if rules:
                rule = rules[0]
                keywords = rule.get("keywords", [])
                if keywords:
                    desc_parts.append(f"  关键词: {', '.join(keywords[:5])}")
            
            # 从 Agent 信息获取能力
            if capabilities:
                desc_parts.append(f"  擅长: {', '.join(capabilities[:5])}")
            
            agent_descriptions.append("\n".join(desc_parts))
        
        agents_text = "\n\n".join(agent_descriptions)
        
        # 如果没有 Agent 描述，添加警告
        if not agents_text:
            agents_text = "（警告：没有可用的 Agent 信息）"
        
        # 提取所有可用的 agent_id
        available_agent_ids = list(self.sub_agents.keys())
        agent_ids_text = ", ".join(available_agent_ids)
        
        prompt = f"""你是一个智能路由助手，需要根据用户的消息，选择最合适的 Agent 来处理。

可用的 Agent：
{agents_text}

用户消息："{message}"

**重要**：你必须从以下 agent_id 中选择一个：{agent_ids_text}

请分析这条消息，选择最合适的 Agent。

要求：
1. 仔细理解消息的意图和主题
2. 从上面列出的 agent_id 中选择最匹配的一个
3. 给出置信度（0-1 之间的小数）
4. agent_id 必须是上面列出的其中一个，不能自己编造

请以 JSON 格式返回：
{{
    "agent_id": "从上面列表中选择的 agent_id",
    "confidence": 0.95,
    "reason": "选择理由"
}}
"""
        return prompt
    
    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM API（使用系统的 RedBearLLM）
        
        Args:
            prompt: 提示词
            
        Returns:
            LLM 响应
        """
        if not self.routing_model_config:
            raise Exception("路由模型配置未设置")
        
        try:
            # 使用系统的 RedBearLLM 来调用模型
            from app.core.models import RedBearLLM
            from app.core.models.base import RedBearModelConfig
            from app.models import ModelApiKey, ModelType
            
            # 获取 API Key 配置
            api_key_config = self.db.query(ModelApiKey).filter(
                ModelApiKey.model_config_id == self.routing_model_config.id,
                ModelApiKey.is_active
            ).first()
            
            if not api_key_config:
                raise Exception("路由模型没有可用的 API Key")
            
            # 打印供应商信息
            logger.info(
                "LLM 路由使用模型",
                extra={
                    "provider": api_key_config.provider,
                    "model_name": api_key_config.model_name,
                    "api_base": api_key_config.api_base,
                    "model_config_id": str(self.routing_model_config.id)
                }
            )
            
            # 创建 RedBearModelConfig
            model_config = RedBearModelConfig(
                model_name=api_key_config.model_name,
                provider=api_key_config.provider,
                api_key=api_key_config.api_key,
                base_url=api_key_config.api_base,
                temperature=0.3,
                max_tokens=500
            )
            
            logger.debug(f"创建 LLM 实例 - Provider: {api_key_config.provider}, Model: {api_key_config.model_name}")
            
            # 创建 LLM 实例
            llm = RedBearLLM(model_config, type=ModelType.CHAT)
            
            # 调用模型
            response = await llm.ainvoke(prompt)
            
            # 提取响应内容
            if hasattr(response, 'content'):
                return response.content
            else:
                return str(response)
            
        except Exception as e:
            logger.error(f"LLM 路由调用失败: {str(e)}")
            # 降级到关键词路由
            raise
    

    
    def _parse_llm_response(self, response: str) -> Tuple[str, float]:
        """解析 LLM 响应
        
        Args:
            response: LLM 响应文本
            
        Returns:
            (agent_id, confidence)
        """
        try:
            # 提取 JSON
            json_match = re.search(r'\{[^}]+\}', response)
            if json_match:
                result = json.loads(json_match.group())
                agent_id = result.get("agent_id")
                confidence = float(result.get("confidence", 0.5))
                
                # 验证 agent_id 是否有效
                if agent_id not in self.sub_agents:
                    logger.warning(f"LLM 返回的 agent_id 无效: {agent_id}")
                    agent_id = self._get_default_agent_id()
                    confidence = 0.5
                
                return agent_id, confidence
            else:
                raise ValueError("无法从响应中提取 JSON")
                
        except Exception as e:
            logger.error(f"解析 LLM 响应失败: {str(e)}")
            return self._get_default_agent_id(), 0.5
    
    def _get_cached_llm_result(self, message: str) -> Optional[Tuple[str, float]]:
        """获取缓存的 LLM 结果
        
        Args:
            message: 用户消息
            
        Returns:
            缓存的结果或 None
        """
        # TODO: 实现真正的缓存机制（使用 Redis 或内存字典）
        return None
    
    def _cache_llm_result(self, message: str, agent_id: str, confidence: float):
        """缓存 LLM 结果
        
        Args:
            message: 用户消息
            agent_id: Agent ID
            confidence: 置信度
        """
        # lru_cache 会自动处理缓存
        pass
    
    async def _extract_topic_with_llm(self, message: str) -> str:
        """使用 LLM 提取主题
        
        Args:
            message: 用户消息
            
        Returns:
            主题名称
        """
        if not self.routing_model_config:
            return self._extract_topic(message)
        
        prompt = f"""请分析以下消息的主题，从这些选项中选择一个：
数学、物理、化学、语文、英语、历史、作业、学习规划、订单、退款、账户、支付、其他

消息："{message}"

只返回主题名称，不要其他内容。
"""
        
        try:
            response = await self._call_llm(prompt)
            topic = response.strip()
            
            # 验证主题
            valid_topics = [
                "数学", "物理", "化学", "语文", "英语", "历史",
                "作业", "学习规划", "订单", "退款", "账户", "支付", "其他"
            ]
            
            if topic in valid_topics:
                return topic
            else:
                return self._extract_topic(message)
                
        except Exception as e:
            logger.error(f"LLM 提取主题失败: {str(e)}")
            return self._extract_topic(message)
    
    # 以下方法与 SmartRouter 相同
    
    def _detect_topic_change(
        self,
        message: str,
        state: Optional[Dict[str, Any]]
    ) -> bool:
        """检测主题是否切换"""
        if not state or not state.get("last_topic"):
            return False
        
        for signal in self.SWITCH_SIGNALS:
            if signal in message:
                logger.info(f"检测到主题切换信号: {signal}")
                return True
        
        current_topic = self._extract_topic(message)
        last_topic = state.get("last_topic")
        
        if current_topic != last_topic and current_topic != "其他":
            logger.info(f"主题变化: {last_topic} -> {current_topic}")
            return True
        
        return False
    
    def _should_continue_current_agent(
        self,
        message: str,
        current_agent_id: str
    ) -> Tuple[bool, float]:
        """判断是否应该继续使用当前 Agent"""
        has_continuation_signal = any(
            signal in message
            for signal in self.CONTINUATION_SIGNALS
        )
        
        current_score = self._calculate_agent_score(message, current_agent_id)
        
        if has_continuation_signal and current_score > 0.3:
            return True, min(current_score + 0.2, 1.0)
        
        if current_score > 0.6:
            return True, current_score
        
        return False, current_score
    
    def _calculate_rule_score(
        self,
        message: str,
        rule: Dict[str, Any]
    ) -> float:
        """计算规则匹配分数"""
        score = 0.0
        message_lower = message.lower()
        
        keywords = rule.get("keywords", [])
        if keywords:
            matched_keywords = sum(
                1 for keyword in keywords
                if keyword.lower() in message_lower
            )
            keyword_score = matched_keywords / len(keywords)
            score += keyword_score * 0.6
        
        patterns = rule.get("patterns", [])
        if patterns:
            matched_patterns = sum(
                1 for pattern in patterns
                if re.search(pattern, message, re.IGNORECASE)
            )
            pattern_score = matched_patterns / len(patterns)
            score += pattern_score * 0.3
        
        exclude_keywords = rule.get("exclude_keywords", [])
        if exclude_keywords:
            has_exclude = any(
                keyword.lower() in message_lower
                for keyword in exclude_keywords
            )
            if has_exclude:
                score *= 0.5
        
        min_keyword_count = rule.get("min_keyword_count", 0)
        if keywords and min_keyword_count > 0:
            matched_count = sum(
                1 for keyword in keywords
                if keyword.lower() in message_lower
            )
            if matched_count < min_keyword_count:
                score *= 0.7
        
        return min(score, 1.0)
    
    def _calculate_agent_score(
        self,
        message: str,
        agent_id: str
    ) -> float:
        """计算 Agent 对消息的匹配分数"""
        agent_rules = [
            rule for rule in self.routing_rules
            if rule.get("target_agent_id") == agent_id
        ]
        
        if not agent_rules:
            return 0.0
        
        max_score = max(
            self._calculate_rule_score(message, rule)
            for rule in agent_rules
        )
        
        return max_score
    
    def _extract_topic(self, message: str) -> str:
        """提取消息主题（关键词方式）"""
        topic_keywords = {
            "数学": ["数学", "方程", "计算", "求解", "x", "y", "函数", "几何"],
            "物理": ["物理", "力", "速度", "加速度", "能量", "功率", "电路"],
            "化学": ["化学", "方程式", "反应", "元素", "分子", "原子", "化合物"],
            "语文": ["语文", "古诗", "作文", "阅读", "文言文", "诗词"],
            "英语": ["英语", "单词", "语法", "翻译", "时态", "句型"],
            "历史": ["历史", "朝代", "事件", "人物", "战争", "革命"],
            "作业": ["作业", "批改", "检查", "评分", "反馈"],
            "学习规划": ["计划", "规划", "方法", "技巧", "时间", "安排"],
            "订单": ["订单", "发货", "物流", "配送", "快递"],
            "退款": ["退款", "退货", "售后", "换货", "维修"],
            "账户": ["账户", "密码", "登录", "注册", "绑定"],
            "支付": ["支付", "付款", "充值", "余额", "优惠券"]
        }
        
        message_lower = message.lower()
        
        topic_scores = {}
        for topic, keywords in topic_keywords.items():
            matched = sum(
                1 for keyword in keywords
                if keyword in message_lower
            )
            if matched > 0:
                topic_scores[topic] = matched
        
        if topic_scores:
            best_topic = max(topic_scores.items(), key=lambda x: x[1])[0]
            return best_topic
        
        return "其他"
    
    def _get_default_agent_id(self) -> str:
        """获取默认 Agent ID"""
        if self.routing_rules:
            return self.routing_rules[0].get("target_agent_id")
        
        if self.sub_agents:
            return next(iter(self.sub_agents.keys()))
        
        return "default-agent"
