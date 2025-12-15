"""智能路由器 - 解决多轮对话路由错乱"""
import re
from typing import Dict, Any, List, Optional, Tuple
from app.services.conversation_state_manager import ConversationStateManager
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class SmartRouter:
    """智能路由器
    
    核心功能：
    1. 检测主题切换
    2. 判断是否应该继续使用当前 Agent
    3. 智能选择最合适的 Agent
    4. 支持强制重新路由
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
        state_manager: ConversationStateManager,
        routing_rules: List[Dict[str, Any]],
        sub_agents: Dict[str, Any]
    ):
        """初始化智能路由器
        
        Args:
            state_manager: 会话状态管理器
            routing_rules: 路由规则列表
            sub_agents: 子 Agent 配置字典
        """
        self.state_manager = state_manager
        self.routing_rules = routing_rules
        self.sub_agents = sub_agents
        
        # 配置参数
        self.min_confidence_for_switch = 0.7  # 切换 Agent 的最小置信度
        self.max_same_agent_turns = 10  # 同一 Agent 最大连续轮数
    
    async def route(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        force_new: bool = False
    ) -> Dict[str, Any]:
        """智能路由
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            force_new: 是否强制重新路由（忽略历史）
            
        Returns:
            路由结果 {
                "agent_id": str,
                "confidence": float,
                "strategy": str,
                "topic": str,
                "topic_changed": bool,
                "reason": str
            }
        """
        logger.info(
            "开始智能路由",
            extra={
                "message_length": len(message),
                "conversation_id": conversation_id,
                "force_new": force_new
            }
        )
        
        # 1. 获取会话状态
        state = None
        if conversation_id and not force_new:
            state = self.state_manager.get_state(conversation_id)
        
        # 2. 检测主题切换
        topic_changed = self._detect_topic_change(message, state)
        
        # 3. 提取当前主题
        topic = self._extract_topic(message)
        
        # 4. 选择路由策略
        if force_new:
            # 强制重新路由
            agent_id, confidence = self._route_from_scratch(message)
            strategy = "force_new"
            reason = "用户强制重新路由"
            
        elif not state or not state.get("current_agent_id"):
            # 新会话，从头路由
            agent_id, confidence = self._route_from_scratch(message)
            strategy = "new_conversation"
            reason = "新会话，首次路由"
            
        elif topic_changed:
            # 主题切换，重新路由
            agent_id, confidence = self._route_from_scratch(message)
            strategy = "topic_changed"
            reason = f"检测到主题切换: {state.get('last_topic')} -> {topic}"
            
        elif state.get("same_agent_turns", 0) >= self.max_same_agent_turns:
            # 同一 Agent 使用太久，强制重新评估
            agent_id, confidence = self._route_from_scratch(message)
            strategy = "max_turns_reached"
            reason = f"同一 Agent 已使用 {state['same_agent_turns']} 轮"
            
        else:
            # 检查是否应该继续使用当前 Agent
            current_agent_id = state["current_agent_id"]
            should_continue, continue_confidence = self._should_continue_current_agent(
                message,
                current_agent_id
            )
            
            if should_continue:
                # 继续使用当前 Agent
                agent_id = current_agent_id
                confidence = continue_confidence
                strategy = "continue_current"
                reason = "消息在当前 Agent 能力范围内"
            else:
                # 重新路由
                new_agent_id, new_confidence = self._route_from_scratch(message)
                
                # 只有新 Agent 的置信度明显更高时才切换
                if new_confidence > continue_confidence + self.min_confidence_for_switch:
                    agent_id = new_agent_id
                    confidence = new_confidence
                    strategy = "switch_agent"
                    reason = f"新 Agent 置信度更高: {new_confidence:.2f} vs {continue_confidence:.2f}"
                else:
                    # 置信度差距不大，继续使用当前 Agent
                    agent_id = current_agent_id
                    confidence = continue_confidence
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
            "reason": reason
        }
        
        logger.info(
            "路由完成",
            extra={
                "agent_id": agent_id,
                "strategy": strategy,
                "confidence": confidence,
                "topic": topic
            }
        )
        
        return result
    
    def _detect_topic_change(
        self,
        message: str,
        state: Optional[Dict[str, Any]]
    ) -> bool:
        """检测主题是否切换
        
        Args:
            message: 用户消息
            state: 会话状态
            
        Returns:
            是否切换主题
        """
        if not state or not state.get("last_topic"):
            return False
        
        # 检查明确的切换信号
        for signal in self.SWITCH_SIGNALS:
            if signal in message:
                logger.info(f"检测到主题切换信号: {signal}")
                return True
        
        # 比较主题
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
        """判断是否应该继续使用当前 Agent
        
        Args:
            message: 用户消息
            current_agent_id: 当前 Agent ID
            
        Returns:
            (是否继续, 置信度)
        """
        # 检查延续信号
        has_continuation_signal = any(
            signal in message
            for signal in self.CONTINUATION_SIGNALS
        )
        
        # 计算当前 Agent 对消息的匹配度
        current_score = self._calculate_agent_score(message, current_agent_id)
        
        # 如果有延续信号且匹配度不太低，继续使用
        if has_continuation_signal and current_score > 0.3:
            return True, min(current_score + 0.2, 1.0)
        
        # 如果匹配度高，继续使用
        if current_score > 0.6:
            return True, current_score
        
        return False, current_score
    
    def _route_from_scratch(self, message: str) -> Tuple[str, float]:
        """从头开始路由（不考虑历史）
        
        Args:
            message: 用户消息
            
        Returns:
            (Agent ID, 置信度)
        """
        best_agent_id = None
        best_score = 0.0
        
        # 遍历所有路由规则
        for rule in self.routing_rules:
            score = self._calculate_rule_score(message, rule)
            
            if score > best_score:
                best_score = score
                best_agent_id = rule.get("target_agent_id")
        
        # 如果没有匹配的规则，使用默认 Agent
        if not best_agent_id or best_score < 0.3:
            best_agent_id = self._get_default_agent_id()
            best_score = 0.5
            logger.warning(f"未找到匹配规则，使用默认 Agent: {best_agent_id}")
        
        return best_agent_id, best_score
    
    def _calculate_rule_score(
        self,
        message: str,
        rule: Dict[str, Any]
    ) -> float:
        """计算规则匹配分数
        
        Args:
            message: 用户消息
            rule: 路由规则
            
        Returns:
            匹配分数 (0-1)
        """
        score = 0.0
        message_lower = message.lower()
        
        # 1. 关键词匹配 (权重 0.6)
        keywords = rule.get("keywords", [])
        if keywords:
            matched_keywords = sum(
                1 for keyword in keywords
                if keyword.lower() in message_lower
            )
            keyword_score = matched_keywords / len(keywords)
            score += keyword_score * 0.6
        
        # 2. 正则匹配 (权重 0.3)
        patterns = rule.get("patterns", [])
        if patterns:
            matched_patterns = sum(
                1 for pattern in patterns
                if re.search(pattern, message, re.IGNORECASE)
            )
            pattern_score = matched_patterns / len(patterns)
            score += pattern_score * 0.3
        
        # 3. 排除关键词 (负分)
        exclude_keywords = rule.get("exclude_keywords", [])
        if exclude_keywords:
            has_exclude = any(
                keyword.lower() in message_lower
                for keyword in exclude_keywords
            )
            if has_exclude:
                score *= 0.5  # 减半
        
        # 4. 最小关键词数量要求
        min_keyword_count = rule.get("min_keyword_count", 0)
        if keywords and min_keyword_count > 0:
            matched_count = sum(
                1 for keyword in keywords
                if keyword.lower() in message_lower
            )
            if matched_count < min_keyword_count:
                score *= 0.7  # 惩罚
        
        return min(score, 1.0)
    
    def _calculate_agent_score(
        self,
        message: str,
        agent_id: str
    ) -> float:
        """计算 Agent 对消息的匹配分数
        
        Args:
            message: 用户消息
            agent_id: Agent ID
            
        Returns:
            匹配分数 (0-1)
        """
        # 找到该 Agent 对应的所有规则
        agent_rules = [
            rule for rule in self.routing_rules
            if rule.get("target_agent_id") == agent_id
        ]
        
        if not agent_rules:
            return 0.0
        
        # 返回最高分数
        max_score = max(
            self._calculate_rule_score(message, rule)
            for rule in agent_rules
        )
        
        return max_score
    
    def _extract_topic(self, message: str) -> str:
        """提取消息主题
        
        Args:
            message: 用户消息
            
        Returns:
            主题名称
        """
        # 主题关键词映射
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
        
        # 统计每个主题的匹配度
        topic_scores = {}
        for topic, keywords in topic_keywords.items():
            matched = sum(
                1 for keyword in keywords
                if keyword in message_lower
            )
            if matched > 0:
                topic_scores[topic] = matched
        
        # 返回匹配度最高的主题
        if topic_scores:
            best_topic = max(topic_scores.items(), key=lambda x: x[1])[0]
            return best_topic
        
        return "其他"
    
    def _get_default_agent_id(self) -> str:
        """获取默认 Agent ID
        
        Returns:
            默认 Agent ID
        """
        # 优先使用第一个路由规则的 Agent
        if self.routing_rules:
            return self.routing_rules[0].get("target_agent_id")
        
        # 否则使用第一个子 Agent
        if self.sub_agents:
            return next(iter(self.sub_agents.keys()))
        
        return "default-agent"
