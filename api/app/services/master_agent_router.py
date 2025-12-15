"""Master Agent 路由器 - 让 Master Agent 真正成为决策中心"""
import json
import re
import uuid
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.services.conversation_state_manager import ConversationStateManager
from app.models import ModelConfig, AgentConfig
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class MasterAgentRouter:
    """Master Agent 路由器
    
    让 Master Agent 作为"大脑"，负责：
    1. 分析用户意图
    2. 选择最合适的 Sub Agent
    3. 决定是否需要多 Agent 协作
    4. 管理会话上下文
    
    优势：
    - 更智能的决策（基于完整上下文）
    - 减少 LLM 调用次数
    - 架构更清晰（Master Agent 真正起作用）
    """
    
    def __init__(
        self,
        db: Session,
        master_agent_config: AgentConfig,
        master_model_config: ModelConfig,
        sub_agents: Dict[str, Any],
        state_manager: ConversationStateManager,
        enable_rule_fast_path: bool = True
    ):
        """初始化 Master Agent 路由器
        
        Args:
            db: 数据库会话
            master_agent_config: Master Agent 配置
            master_model_config: Master Agent 使用的模型配置
            sub_agents: 子 Agent 配置字典
            state_manager: 会话状态管理器
            enable_rule_fast_path: 是否启用规则快速路径（性能优化）
        """
        self.db = db
        self.master_agent_config = master_agent_config
        self.master_model_config = master_model_config
        self.sub_agents = sub_agents
        self.state_manager = state_manager
        self.enable_rule_fast_path = enable_rule_fast_path
        
        logger.info(
            "Master Agent 路由器初始化",
            extra={
                "master_agent": master_agent_config.name,
                "sub_agent_count": len(sub_agents),
                "enable_rule_fast_path": enable_rule_fast_path
            }
        )
    
    async def route(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """智能路由决策
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            variables: 变量参数
            
        Returns:
            路由决策结果
        """
        logger.info(
            "开始 Master Agent 路由",
            extra={
                "message_length": len(message),
                "conversation_id": conversation_id
            }
        )
        
        # 1. 获取会话状态
        state = None
        if conversation_id:
            state = self.state_manager.get_state(conversation_id)
        
        # 2. 尝试规则快速路径（可选的性能优化）
        if self.enable_rule_fast_path:
            rule_result = self._try_rule_fast_path(message, state)
            if rule_result:
                logger.info(
                    "规则快速路径命中",
                    extra={
                        "agent_id": rule_result["selected_agent_id"],
                        "confidence": rule_result["confidence"]
                    }
                )
                
                # 更新会话状态
                if conversation_id:
                    self.state_manager.update_state(
                        conversation_id,
                        rule_result["selected_agent_id"],
                        message,
                        rule_result.get("topic"),
                        rule_result["confidence"]
                    )
                
                return rule_result
        
        # 3. 调用 Master Agent 做决策
        decision = await self._master_agent_decide(message, state, variables)
        
        # 4. 更新会话状态
        if conversation_id:
            self.state_manager.update_state(
                conversation_id,
                decision["selected_agent_id"],
                message,
                decision.get("topic"),
                decision["confidence"]
            )
        
        logger.info(
            "Master Agent 路由完成",
            extra={
                "agent_id": decision["selected_agent_id"],
                "strategy": decision["strategy"],
                "confidence": decision["confidence"]
            }
        )
        
        return decision
    
    def _try_rule_fast_path(
        self,
        message: str,
        state: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """尝试规则快速路径（性能优化）
        
        对于明确的关键词匹配，直接返回结果，不调用 Master Agent
        
        Args:
            message: 用户消息
            state: 会话状态
            
        Returns:
            如果命中规则返回决策结果，否则返回 None
        """
        # 定义高置信度关键词规则
        high_confidence_rules = [
            {
                "keywords": ["数学", "方程", "计算", "求解"],
                "agent_role": "数学",
                "confidence_threshold": 0.9
            },
            {
                "keywords": ["物理", "力学", "电路", "光学"],
                "agent_role": "物理",
                "confidence_threshold": 0.9
            },
            {
                "keywords": ["订单", "发货", "物流", "快递"],
                "agent_role": "订单",
                "confidence_threshold": 0.9
            },
            {
                "keywords": ["退款", "退货", "售后"],
                "agent_role": "退款",
                "confidence_threshold": 0.9
            }
        ]
        
        message_lower = message.lower()
        
        for rule in high_confidence_rules:
            matched_keywords = [kw for kw in rule["keywords"] if kw in message_lower]
            
            if matched_keywords:
                confidence = len(matched_keywords) / len(rule["keywords"])
                
                if confidence >= rule["confidence_threshold"]:
                    # 查找对应的 agent
                    for agent_id, agent_data in self.sub_agents.items():
                        agent_info = agent_data.get("info", {})
                        if agent_info.get("role") == rule["agent_role"]:
                            return {
                                "selected_agent_id": agent_id,
                                "confidence": confidence,
                                "strategy": "rule_fast_path",
                                "reasoning": f"关键词匹配: {', '.join(matched_keywords)}",
                                "topic": rule["agent_role"],
                                "need_collaboration": False,
                                "routing_method": "rule"
                            }
        
        return None
    
    async def _master_agent_decide(
        self,
        message: str,
        state: Optional[Dict[str, Any]],
        variables: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """让 Master Agent 做路由决策
        
        Args:
            message: 用户消息
            state: 会话状态
            variables: 变量参数
            
        Returns:
            决策结果
        """
        # 1. 构建决策 prompt
        prompt = self._build_decision_prompt(message, state, variables)
        
        # 2. 调用 Master Agent 的 LLM
        try:
            response = await self._call_master_agent_llm(prompt)
            
            # 3. 解析决策
            decision = self._parse_decision(response)
            
            # 4. 验证决策
            decision = self._validate_decision(decision)
            
            return decision
            
        except Exception as e:
            logger.error(f"Master Agent 决策失败: {str(e)}")
            # 降级到默认 agent
            return self._get_fallback_decision(message)
    
    def _build_decision_prompt(
        self,
        message: str,
        state: Optional[Dict[str, Any]],
        variables: Optional[Dict[str, Any]]
    ) -> str:
        """构建 Master Agent 的决策 prompt
        
        Args:
            message: 用户消息
            state: 会话状态
            variables: 变量参数
            
        Returns:
            prompt 字符串
        """
        # 1. 构建 Sub Agent 描述（简化版，提升性能）
        agent_descriptions = []
        for agent_id, agent_data in self.sub_agents.items():
            agent_info = agent_data.get("info", {})
            
            name = agent_info.get("name", "未命名")
            role = agent_info.get("role", "")
            capabilities = agent_info.get("capabilities", [])
            
            # 简化格式：一行描述
            desc = f"- {agent_id}: {name}"
            if role:
                desc += f" ({role})"
            if capabilities:
                desc += f" - {', '.join(capabilities[:3])}"  # 只取前3个能力
            
            agent_descriptions.append(desc)
        
        agents_text = "\n".join(agent_descriptions)
        
        # 2. 构建会话上下文
        context_text = ""
        if state:
            current_agent = state.get("current_agent_id")
            last_topic = state.get("last_topic")
            same_turns = state.get("same_agent_turns", 0)
            
            if current_agent:
                context_text = f"""
当前会话上下文：
- 当前使用的 Agent: {current_agent}
- 上一个主题: {last_topic}
- 连续使用轮数: {same_turns}
"""
        
        # 获取第一个可用的 agent_id 作为示例
        example_agent_id = next(iter(self.sub_agents.keys())) if self.sub_agents else "agent_id"
        
        # 3. 构建完整 prompt（简化版，提升性能）
        prompt = f"""路由任务：分析问题并选择合适的 Agent。

可用 Agent：
{agents_text}
{context_text}
问题："{message}"

返回 JSON 格式决策：

**情况1：单一问题（最常见）**
{{"selected_agent_id": "{example_agent_id}", "confidence": 0.9, "need_collaboration": false, "reasoning": "选择理由"}}

**情况2：需要拆分成多个独立子问题**
当用户问题包含多个完全独立的子问题时使用（如"写诗+做数学题"）。
必须提供 sub_questions 数组，每个子问题必须指定 agent_id：
{{"selected_agent_id": "{example_agent_id}", "confidence": 0.9, "need_collaboration": true, "need_decomposition": true,
 "sub_questions": [
   {{"question": "具体的子问题1", "agent_id": "{example_agent_id}", "order": 1, "depends_on": []}},
   {{"question": "具体的子问题2", "agent_id": "{example_agent_id}", "order": 2, "depends_on": []}}
 ],
 "collaboration_strategy": "decomposition", "reasoning": "问题包含X个独立子问题"}}

**情况3：需要多个Agent协作分析同一问题**
{{"selected_agent_id": "{example_agent_id}", "confidence": 0.9, "need_collaboration": true,
 "collaboration_agents": [{{"agent_id": "{example_agent_id}", "role": "primary", "task": "主要任务", "order": 1}}],
 "collaboration_strategy": "sequential", "reasoning": "需要多角度分析"}}

重要规则：
1. selected_agent_id 必须从上面的可用 Agent 列表中选择
2. 如果选择情况2（拆分），sub_questions 数组不能为空，必须包含具体的子问题
3. 每个子问题的 agent_id 也必须从可用列表中选择
4. depends_on 表示依赖关系（如 [1] 表示依赖第1个子问题的结果）
5. 大多数情况应该选择情况1（单一Agent），只有明确需要时才拆分或协作
6. 只做路由决策，不要回答问题内容

请返回 JSON："""
        
        return prompt
    
    async def _call_master_agent_llm(self, prompt: str) -> str:
        """调用 Master Agent 的 LLM
        
        Args:
            prompt: 提示词
            
        Returns:
            LLM 响应
        """
        try:
            from app.core.models import RedBearLLM
            from app.core.models.base import RedBearModelConfig
            from app.models import ModelApiKey, ModelType
            
            # 获取 API Key 配置
            api_key_config = self.db.query(ModelApiKey).filter(
                ModelApiKey.model_config_id == self.master_model_config.id,
                ModelApiKey.is_active == True
            ).first()
            
            if not api_key_config:
                raise Exception("Master Agent 模型没有可用的 API Key")
            
            logger.info(
                "调用 Master Agent LLM",
                extra={
                    "provider": api_key_config.provider,
                    "model_name": api_key_config.model_name
                }
            )
            
            # 创建 RedBearModelConfig
            model_config = RedBearModelConfig(
                model_name=api_key_config.model_name,
                provider=api_key_config.provider,
                api_key=api_key_config.api_key,
                base_url=api_key_config.api_base,
                temperature=0.3,  # 决策任务使用较低温度
                max_tokens=1000
            )
            
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
            logger.error(f"Master Agent LLM 调用失败: {str(e)}")
            raise
    
    def _parse_decision(self, response: str) -> Dict[str, Any]:
        """解析 Master Agent 的决策
        
        Args:
            response: LLM 响应
            
        Returns:
            决策字典
        """
        try:
            # 提取 JSON
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group())
                
                # 添加默认值
                decision.setdefault("confidence", 0.8)
                decision.setdefault("strategy", "master_agent")
                decision.setdefault("routing_method", "master_agent")
                decision.setdefault("need_collaboration", False)
                decision.setdefault("collaboration_agents", [])
                
                return decision
            else:
                raise ValueError("无法从响应中提取 JSON")
                
        except Exception as e:
            logger.error(f"解析 Master Agent 决策失败: {str(e)}")
            logger.debug(f"原始响应: {response}")
            raise
    
    def _validate_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """验证决策的有效性
        
        Args:
            decision: 决策字典
            
        Returns:
            验证后的决策
        """
        # 验证 agent_id
        selected_agent_id = decision.get("selected_agent_id")
        if selected_agent_id not in self.sub_agents:
            logger.warning(f"Master Agent 返回的 agent_id 无效: {selected_agent_id}")
            # 使用默认 agent
            decision["selected_agent_id"] = self._get_default_agent_id()
            decision["confidence"] = 0.5
            decision["reasoning"] = "原始选择无效，使用默认 Agent"
        
        # 验证 confidence
        confidence = decision.get("confidence", 0.8)
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            decision["confidence"] = 0.8
        
        # 验证协作 agents
        if decision.get("need_collaboration"):
            # 检查是否是问题拆分模式
            if decision.get("need_decomposition") or decision.get("sub_questions"):
                # 问题拆分模式
                sub_questions = decision.get("sub_questions", [])
                
                # 验证每个子问题
                valid_sub_questions = []
                for sub_q in sub_questions:
                    if isinstance(sub_q, dict):
                        agent_id = sub_q.get("agent_id")
                        question = sub_q.get("question")
                        
                        if agent_id in self.sub_agents and question:
                            # 确保有必要的字段
                            sub_q.setdefault("order", len(valid_sub_questions) + 1)
                            sub_q.setdefault("depends_on", [])
                            valid_sub_questions.append(sub_q)
                        else:
                            # 记录验证失败的原因
                            logger.warning(
                                "子问题验证失败",
                                extra={
                                    "agent_id": agent_id,
                                    "has_question": bool(question),
                                    "agent_exists": agent_id in self.sub_agents if agent_id else False,
                                    "available_agents": list(self.sub_agents.keys())
                                }
                            )
                
                decision["sub_questions"] = valid_sub_questions
                
                # 如果所有子问题都验证失败，降级处理
                if not valid_sub_questions and sub_questions:
                    logger.warning(
                        "所有子问题验证失败，降级到单 Agent 模式",
                        extra={
                            "original_sub_question_count": len(sub_questions),
                            "available_agents": list(self.sub_agents.keys())
                        }
                    )
                    # 降级：取消协作标记，使用默认 Agent
                    decision["need_collaboration"] = False
                    decision["need_decomposition"] = False
                    decision["collaboration_strategy"] = None
                    # 选择第一个可用的 Agent
                    if self.sub_agents:
                        first_agent_id = next(iter(self.sub_agents.keys()))
                        decision["selected_agent_id"] = first_agent_id
                        logger.info(f"降级使用默认 Agent: {first_agent_id}")
                
                # 设置协作策略为 decomposition
                decision["collaboration_strategy"] = "decomposition"
                
                logger.info(
                    "问题拆分决策验证完成",
                    extra={
                        "sub_question_count": len(valid_sub_questions),
                        "strategy": "decomposition"
                    }
                )
            else:
                # 普通协作模式
                collaboration_agents = decision.get("collaboration_agents", [])
                
                # 如果是简单列表格式，转换为详细格式
                if collaboration_agents and isinstance(collaboration_agents[0], str):
                    collaboration_agents = [
                        {
                            "agent_id": agent_id,
                            "role": "primary" if i == 0 else "secondary",
                            "task": "协作处理",
                            "order": i + 1
                        }
                        for i, agent_id in enumerate(collaboration_agents)
                    ]
                
                # 验证每个协作 agent
                valid_agents = []
                for agent_info in collaboration_agents:
                    if isinstance(agent_info, dict):
                        agent_id = agent_info.get("agent_id")
                        if agent_id in self.sub_agents:
                            # 确保有必要的字段
                            agent_info.setdefault("role", "secondary")
                            agent_info.setdefault("task", "协作处理")
                            agent_info.setdefault("order", len(valid_agents) + 1)
                            valid_agents.append(agent_info)
                    elif isinstance(agent_info, str) and agent_info in self.sub_agents:
                        valid_agents.append({
                            "agent_id": agent_info,
                            "role": "secondary",
                            "task": "协作处理",
                            "order": len(valid_agents) + 1
                        })
                
                decision["collaboration_agents"] = valid_agents
                
                # 设置默认协作策略
                if not decision.get("collaboration_strategy"):
                    decision["collaboration_strategy"] = "sequential"
                
                logger.info(
                    "协作决策验证完成",
                    extra={
                        "collaboration_agent_count": len(valid_agents),
                        "strategy": decision.get("collaboration_strategy")
                    }
                )
        
        return decision
    
    def _get_fallback_decision(self, message: str) -> Dict[str, Any]:
        """获取降级决策（当 Master Agent 失败时）
        
        Args:
            message: 用户消息
            
        Returns:
            降级决策
        """
        default_agent_id = self._get_default_agent_id()
        
        return {
            "selected_agent_id": default_agent_id,
            "confidence": 0.5,
            "strategy": "fallback",
            "reasoning": "Master Agent 决策失败，使用默认 Agent",
            "topic": "未知",
            "need_collaboration": False,
            "collaboration_agents": [],
            "routing_method": "fallback"
        }
    
    def _get_default_agent_id(self) -> str:
        """获取默认 Agent ID
        
        Returns:
            默认 Agent ID
        """
        if self.sub_agents:
            # 返回第一个 agent
            return next(iter(self.sub_agents.keys()))
        
        return "default-agent"
