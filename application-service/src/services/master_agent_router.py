"""Master Agent router - makes the Master Agent the true decision center."""
import logging

import json
import re
import uuid
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..db import end_read_transaction
from ..schemas.app_schema import ModelParameters
from .conversation_state_manager import ConversationStateManager
from ..models import ModelConfig, AgentConfig
from .model_service import ModelConfigService

logger = logging.getLogger(__name__)


class MasterAgentRouter:
    """Master Agent router.

    Treats the Master Agent as the "brain", responsible for:
    1. Analyzing user intent
    2. Selecting the most suitable Sub Agent
    3. Deciding whether multi-agent collaboration is needed
    4. Managing conversation context

    Advantages:
    - Smarter decisions (based on the full context)
    - Fewer LLM calls
    - Clearer architecture (the Master Agent actually drives routing)
    """

    def __init__(
        self,
        db: Session | AsyncSession,
        master_model_config: ModelConfig,
        model_parameters: ModelParameters,
        sub_agents: Dict[str, Any],
        state_manager: ConversationStateManager,
        tenant_id: uuid.UUID | None = None,
    ):
        """Initialize the Master Agent router.

        Args:
            db: database session
            master_model_config: model config used by the Master Agent
            sub_agents: dict of sub-agent configs
            state_manager: conversation state manager

        Note: the keyword-rule fast path (`enable_rule_fast_path`) has been removed --
        it conflicted with the supervisor loop's semantics that dispatch is entirely up
        to the supervisor LLM, and its hardcoded rules had zero overlap with the real
        configuration (the 0.9 confidence threshold contradicted "1 of 4 keywords hit
        = 0.25", so it never actually fired). Deterministic orchestration needs are
        served by the declarative modes (pipeline/fanout/router); no bypass is opened
        in this router.
        """
        self.db = db
        self.master_model_config = master_model_config
        self.model_parameters = model_parameters
        self.sub_agents = sub_agents
        self.state_manager = state_manager
        self.tenant_id = tenant_id

        logger.info(
            "Master Agent 路由器初始化",
            extra={"sub_agent_count": len(sub_agents)}
        )

    async def route(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make an intelligent routing decision.

        Args:
            message: user message
            conversation_id: conversation ID
            variables: variable parameters

        Returns:
            Routing decision result
        """
        logger.info(
            "开始 Master Agent 路由",
            extra={
                "message_length": len(message),
                "conversation_id": conversation_id
            }
        )

        # 1. Fetch conversation state (async interface - production backend is Redis;
        # a read failure must not block routing)
        state = None
        if conversation_id:
            state = await self.state_manager.aget_state(conversation_id)

        # 2. Ask the Master Agent to decide
        # (the former "keyword-rule fast path" branch has been removed; see class docstring)
        decision = await self._master_agent_decide(message, state, variables)

        # 4. Update conversation state
        if conversation_id:
            await self.state_manager.aupdate_state(
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

    async def _master_agent_decide(
        self,
        message: str,
        state: Optional[Dict[str, Any]],
        variables: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Let the Master Agent make the routing decision.

        Args:
            message: user message
            state: conversation state
            variables: variable parameters

        Returns:
            Decision result
        """
        # 1. Build the decision prompt
        prompt = self._build_decision_prompt(message, state, variables)

        # 2. Invoke the Master Agent LLM
        try:
            response = await self._call_master_agent_llm(prompt)

            # 3. Parse the decision
            decision = self._parse_decision(response)

            # 4. Validate the decision
            decision = self._validate_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"Master Agent 决策失败: {str(e)}")
            # Fall back to the default agent
            return self._get_fallback_decision(message)

    def _build_decision_prompt(
        self,
        message: str,
        state: Optional[Dict[str, Any]],
        variables: Optional[Dict[str, Any]]
    ) -> str:
        """Build the decision prompt for the Master Agent.

        Args:
            message: user message
            state: conversation state
            variables: variable parameters

        Returns:
            Prompt string
        """
        # 1. Build sub-agent descriptions (simplified for performance)
        agent_descriptions = []
        for agent_id, agent_data in self.sub_agents.items():
            agent_info = agent_data.get("info", {})

            name = agent_info.get("name", "未命名")
            role = agent_info.get("role", "")
            capabilities = agent_info.get("capabilities", [])

            # Simplified format: one line per agent
            desc = f"- {agent_id}: {name}"
            if role:
                desc += f" ({role})"
            if capabilities:
                desc += f" - {', '.join(capabilities[:3])}"  # take only the first 3 capabilities

            agent_descriptions.append(desc)

        agents_text = "\n".join(agent_descriptions)

        # 2. Build conversation context
        context_text = ""
        if state:
            current_agent = state.get("current_agent_id")
            last_topic = state.get("last_topic")
            same_turns = state.get("same_agent_turns", 0)

            # Versioning strategy: a republished sub-agent gets a new release ID, so
            # the old ID recorded in the conversation state may no longer be in the
            # roster; if stale, skip it as the "current agent" hint and let the LLM
            # re-route
            if current_agent and str(current_agent) not in self.sub_agents:
                current_agent = None

            if current_agent:
                context_text = f"""
当前会话上下文：
- 当前使用的 Agent: {current_agent}
- 上一个主题: {last_topic}
- 连续使用轮数: {same_turns}
"""

        # Get the first available agent_id as the example
        example_agent_id = next(iter(self.sub_agents.keys())) if self.sub_agents else "agent_id"

        # 3. Build the full prompt (simplified for performance)
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
        """Invoke the Master Agent LLM.

        Args:
            prompt: prompt text

        Returns:
            LLM response
        """
        try:
            from ..model_clients import RedBearChatModel

            # Remote mode: the host no longer decrypts credentials; identity/capability
            # come from the non-decrypted view (routing and invocation happen in
            # model-service)
            model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db,
                self.master_model_config.id,
                tenant_id=self.tenant_id,
            )
            # The model view is already materialized; the next step is a long
            # routing LLM call. Release the shared session's connection first
            # (sync-session routers are already blocking paths, so only async
            # sessions need this).
            if isinstance(self.db, AsyncSession):
                await end_read_transaction(self.db, "master_router.model_view")

            logger.info(
                "调用 Master Agent LLM",
                extra={
                    "provider": model_view.provider,
                    "model_name": model_view.model_name
                }
            )
            # temperature = 0.3  # lower temperature for decision tasks
            # max_tokens = 1000            
            # if self.model_parameters:
            #     temperature = self.model_parameters["temperature"]
            #     max_tokens = self.model_parameters["max_tokens"]
            if self.model_parameters:
                if hasattr(self.model_parameters, 'temperature'):
                    # Pydantic model
                    temperature = self.model_parameters.temperature
                    max_tokens = getattr(self.model_parameters, 'max_tokens', 1000)
                elif isinstance(self.model_parameters, dict):
                    # dict
                    temperature = self.model_parameters.get("temperature", 0.3)
                    max_tokens = self.model_parameters.get("max_tokens", 1000)
                else:
                    temperature = 0.3
                    max_tokens = 1000
            else:
                temperature = 0.3
                max_tokens = 1000
            # extra_params = {"temperature": self.model_parameters.get("temperature", 0.3),
            #                     "max_tokens":self.model_parameters.get("max_tokens", 1000)
            #                     }
            extra_params = {"temperature": temperature, "max_tokens": max_tokens}

            # Create the LLM instance
            llm = RedBearChatModel.for_invoke(model_view, params=extra_params)

            # Invoke the model
            response = await llm.ainvoke(prompt)
            # Channel key usage is no longer recorded: invocations carry no credentials
            # (channel counting is settled by model-service from usage events)

            # Extract token usage
            self._last_routing_tokens = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                um = response.usage_metadata
                self._last_routing_tokens = um.get("total_tokens", 0) if isinstance(um, dict) else getattr(um, "total_tokens", 0)
            elif hasattr(response, 'response_metadata') and response.response_metadata:
                token_usage = response.response_metadata.get("token_usage") or response.response_metadata.get("usage", {})
                if isinstance(token_usage, dict):
                    self._last_routing_tokens = token_usage.get("total_tokens", 0)
            logger.info(f"Master Agent 路由 token 消耗: {self._last_routing_tokens}")

            # Extract the response content
            if hasattr(response, 'content'):
                return response.content
            else:
                return str(response)

        except Exception as e:
            logger.error(f"Master Agent LLM 调用失败: {str(e)}")
            raise

    def _parse_decision(self, response: str) -> Dict[str, Any]:
        """Parse the Master Agent's decision.

        Args:
            response: LLM response

        Returns:
            Decision dict
        """
        try:
            # Extract JSON
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group())

                # Apply defaults
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
        """Validate the decision.

        Args:
            decision: decision dict

        Returns:
            The validated decision
        """
        # Validate agent_id
        selected_agent_id = decision.get("selected_agent_id")
        if selected_agent_id not in self.sub_agents:
            logger.warning(f"Master Agent 返回的 agent_id 无效: {selected_agent_id}")
            # Use the default agent
            decision["selected_agent_id"] = self._get_default_agent_id()
            decision["confidence"] = 0.5
            decision["reasoning"] = "原始选择无效，使用默认 Agent"

        # Validate confidence
        confidence = decision.get("confidence", 0.8)
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            decision["confidence"] = 0.8

        # Validate collaboration agents
        if decision.get("need_collaboration"):
            # Check whether this is the question-decomposition mode
            if decision.get("need_decomposition") or decision.get("sub_questions"):
                # Question-decomposition mode
                sub_questions = decision.get("sub_questions", [])

                # Validate each sub-question
                valid_sub_questions = []
                for sub_q in sub_questions:
                    if isinstance(sub_q, dict):
                        agent_id = sub_q.get("agent_id")
                        question = sub_q.get("question")

                        if agent_id in self.sub_agents and question:
                            # Ensure required fields are present
                            sub_q.setdefault("order", len(valid_sub_questions) + 1)
                            sub_q.setdefault("depends_on", [])
                            valid_sub_questions.append(sub_q)
                        else:
                            # Log why validation failed
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

                # If all sub-questions fail validation, degrade gracefully
                if not valid_sub_questions and sub_questions:
                    logger.warning(
                        "所有子问题验证失败，降级到单 Agent 模式",
                        extra={
                            "original_sub_question_count": len(sub_questions),
                            "available_agents": list(self.sub_agents.keys())
                        }
                    )
                    # Degrade: clear the collaboration flags and use the default agent
                    decision["need_collaboration"] = False
                    decision["need_decomposition"] = False
                    decision["collaboration_strategy"] = None
                    # Pick the first available agent
                    if self.sub_agents:
                        first_agent_id = next(iter(self.sub_agents.keys()))
                        decision["selected_agent_id"] = first_agent_id
                        logger.info(f"降级使用默认 Agent: {first_agent_id}")

                # Set the collaboration strategy to decomposition
                decision["collaboration_strategy"] = "decomposition"

                logger.info(
                    "问题拆分决策验证完成",
                    extra={
                        "sub_question_count": len(valid_sub_questions),
                        "strategy": "decomposition"
                    }
                )
            else:
                # Regular collaboration mode
                collaboration_agents = decision.get("collaboration_agents", [])

                # Convert the simple-list format into the detailed format
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

                # Validate each collaboration agent
                valid_agents = []
                for agent_info in collaboration_agents:
                    if isinstance(agent_info, dict):
                        agent_id = agent_info.get("agent_id")
                        if agent_id in self.sub_agents:
                            # Ensure required fields are present
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

                # Set the default collaboration strategy
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
        """Build the fallback decision used when the Master Agent fails.

        Args:
            message: user message

        Returns:
            Fallback decision
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
        """Get the default Agent ID.

        Returns:
            Default Agent ID
        """
        if self.sub_agents:
            # Return the first agent
            return next(iter(self.sub_agents.keys()))

        return "default-agent"
