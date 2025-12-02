"""多 Agent 编排器"""
import uuid
import time
import asyncio
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.models import MultiAgentConfig, AgentConfig, ModelConfig
from app.services.agent_registry import AgentRegistry
from app.services.llm_router import LLMRouter
from app.services.conversation_state_manager import ConversationStateManager
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class MultiAgentOrchestrator:
    """多 Agent 编排器 - 协调多个 Agent 协作完成任务"""
    
    def __init__(self, db: Session, config: MultiAgentConfig):
        """初始化编排器
        
        Args:
            db: 数据库会话
            config: 多 Agent 配置
        """
        self.db = db
        self.config = config
        self.registry = AgentRegistry(db)
        
        # 加载主 Agent
        self.master_agent = self._load_agent(config.master_agent_id)
        
        # 加载子 Agent
        self.sub_agents = {}
        for sub_agent_info in config.sub_agents:
            agent_id = uuid.UUID(sub_agent_info["agent_id"])
            agent = self._load_agent(agent_id)
            self.sub_agents[str(agent_id)] = {
                "config": agent,
                "info": sub_agent_info
            }
        
        # 初始化 LLM 路由器（使用主 Agent 的模型）
        self.llm_router = None
        if self.master_agent and hasattr(self.master_agent, 'default_model_config_id'):
            routing_model = self.db.get(ModelConfig, self.master_agent.default_model_config_id)
            if routing_model:
                state_manager = ConversationStateManager()
                self.llm_router = LLMRouter(
                    db=db,
                    state_manager=state_manager,
                    routing_rules=config.routing_rules or [],
                    sub_agents=self.sub_agents,
                    routing_model_config=routing_model,
                    use_llm=True
                )
                logger.info(
                    f"LLM 路由器已初始化（使用主 Agent 模型）",
                    extra={
                        "routing_model": routing_model.name,
                        "routing_model_id": str(routing_model.id)
                    }
                )
        
        logger.info(
            f"多 Agent 编排器初始化",
            extra={
                "config_id": str(config.id),
                "mode": config.orchestration_mode,
                "sub_agent_count": len(self.sub_agents),
                "has_llm_router": self.llm_router is not None
            }
        )
    
    async def execute_stream(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        use_llm_routing: bool = True,
        web_search: bool = True,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ):
        """执行多 Agent 任务（流式返回）
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            use_llm_routing: 是否使用 LLM 路由
            
        Yields:
            SSE 格式的事件流
        """
        import json
        
        start_time = time.time()
        
        logger.info(
            f"开始执行多 Agent 任务（流式）",
            extra={
                "mode": self.config.orchestration_mode,
                "message_length": len(message)
            }
        )
        
        try:
            # 发送开始事件
            yield self._format_sse_event("start", {
                "mode": self.config.orchestration_mode,
                "timestamp": time.time()
            })
            
            # 1. 主 Agent 分析任务
            task_analysis = await self._analyze_task(message, variables)
            task_analysis["use_llm_routing"] = use_llm_routing
            
            # 2. 根据模式执行（流式）
            if self.config.orchestration_mode == "conditional":
                async for event in self._execute_conditional_stream(
                    task_analysis,
                    conversation_id,
                    user_id,
                    web_search,
                    memory,
                    storage_type,
                    user_rag_memory_id
                ):
                    yield event
            else:
                # 其他模式暂时使用非流式执行，然后一次性返回
                if self.config.orchestration_mode == "sequential":
                    results = await self._execute_sequential(
                        task_analysis,
                        conversation_id,
                        user_id,
                        web_search,
                        memory,
                        storage_type,
                        user_rag_memory_id
                    )
                elif self.config.orchestration_mode == "parallel":
                    results = await self._execute_parallel(
                        task_analysis,
                        conversation_id,
                        user_id,
                        web_search,
                        memory,
                        storage_type,
                        user_rag_memory_id
                    )
                elif self.config.orchestration_mode == "loop":
                    results = await self._execute_loop(
                        task_analysis,
                        conversation_id,
                        user_id,
                        web_search,
                        memory,
                        storage_type,
                        user_rag_memory_id
                    )
                else:
                    raise BusinessException(
                        f"不支持的编排模式: {self.config.orchestration_mode}",
                        BizCode.INVALID_PARAMETER
                    )
                
                # 整合结果
                final_result = await self._aggregate_results(results)
                
                # 提取会话 ID
                sub_conversation_id = None
                if isinstance(results, dict):
                    sub_conversation_id = results.get("conversation_id") or results.get("result", {}).get("conversation_id")
                elif isinstance(results, list) and results:
                    for item in results:
                        if "result" in item:
                            sub_conversation_id = item["result"].get("conversation_id")
                            if sub_conversation_id:
                                break
                
                # 发送消息事件
                yield self._format_sse_event("message", {
                    "content": final_result,
                    "conversation_id": sub_conversation_id
                })
            
            elapsed_time = time.time() - start_time
            
            # 发送结束事件
            yield self._format_sse_event("end", {
                "elapsed_time": elapsed_time,
                "timestamp": time.time()
            })
            
            logger.info(
                f"多 Agent 任务完成（流式）",
                extra={
                    "mode": self.config.orchestration_mode,
                    "elapsed_time": elapsed_time
                }
            )
            
        except Exception as e:
            logger.error(
                f"多 Agent 任务执行失败（流式）",
                extra={"error": str(e), "mode": self.config.orchestration_mode}
            )
            # 发送错误事件
            yield self._format_sse_event("error", {
                "error": str(e),
                "timestamp": time.time()
            })
    
    async def execute(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        use_llm_routing: bool = True,
        web_search: bool = False,
        memory: bool = True
    ) -> Dict[str, Any]:
        """执行多 Agent 任务
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            
        Returns:
            执行结果
        """
        start_time = time.time()
        
        logger.info(
            f"开始执行多 Agent 任务",
            extra={
                "mode": self.config.orchestration_mode,
                "message_length": len(message)
            }
        )
        
        try:
            # 1. 主 Agent 分析任务
            task_analysis = await self._analyze_task(message, variables)
            task_analysis["use_llm_routing"] = use_llm_routing
            
            # 2. 根据模式执行
            if self.config.orchestration_mode == "sequential":
                results = await self._execute_sequential(
                    task_analysis,
                    conversation_id,
                    user_id,
                    web_search,
                    memory
                )
            elif self.config.orchestration_mode == "parallel":
                results = await self._execute_parallel(
                    task_analysis,
                    conversation_id,
                    user_id,
                    web_search,
                    memory
                )
            elif self.config.orchestration_mode == "conditional":
                results = await self._execute_conditional(
                    task_analysis,
                    conversation_id,
                    user_id,
                    web_search,
                    memory
                )
            elif self.config.orchestration_mode == "loop":
                results = await self._execute_loop(
                    task_analysis,
                    conversation_id,
                    user_id,
                    web_search,
                    memory
                )
            else:
                raise BusinessException(
                    f"不支持的编排模式: {self.config.orchestration_mode}",
                    BizCode.INVALID_PARAMETER
                )
            
            # 3. 整合结果
            final_result = await self._aggregate_results(results)
            
            elapsed_time = time.time() - start_time
            
            # 4. 提取子 Agent 的 conversation_id（用于多轮对话）
            sub_conversation_id = None
            if isinstance(results, dict):
                # conditional 或 loop 模式
                sub_conversation_id = results.get("conversation_id") or results.get("result", {}).get("conversation_id")
            elif isinstance(results, list) and results:
                # sequential 或 parallel 模式，使用第一个成功的结果
                for item in results:
                    if "result" in item:
                        sub_conversation_id = item["result"].get("conversation_id")
                        if sub_conversation_id:
                            break
            
            logger.info(
                f"多 Agent 任务完成",
                extra={
                    "mode": self.config.orchestration_mode,
                    "elapsed_time": elapsed_time,
                    "sub_agent_count": len(results) if isinstance(results, list) else 1,
                    "sub_conversation_id": sub_conversation_id
                }
            )
            
            return {
                "message": final_result,
                "conversation_id": sub_conversation_id,  # 返回子 Agent 的会话 ID
                "elapsed_time": elapsed_time,
                "mode": self.config.orchestration_mode,
                "sub_results": results
            }
            
        except Exception as e:
            logger.error(
                f"多 Agent 任务执行失败",
                extra={"error": str(e), "mode": self.config.orchestration_mode}
            )
            raise
    
    async def _analyze_task(
        self,
        message: str,
        variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """主 Agent 分析任务
        
        Args:
            message: 用户消息
            variables: 变量参数
            
        Returns:
            任务分析结果
        """
        # 简化版本：直接返回基本信息
        # 在实际应用中，可以让主 Agent 使用 LLM 分析任务
        return {
            "message": message,
            "variables": variables or {},
            "sub_agents": self.config.sub_agents,
            "initial_context": variables or {}
        }
    
    async def _execute_sequential(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> List[Dict[str, Any]]:
        """顺序执行子 Agent
        
        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Returns:
            执行结果列表
        """
        results = []
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")
        
        # 按优先级排序
        sub_agents = sorted(
            task_analysis["sub_agents"],
            key=lambda x: x.get("priority", 0)
        )
        
        for sub_agent_info in sub_agents:
            agent_id = sub_agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)
            
            if not agent_data:
                logger.warning(f"子 Agent 不存在: {agent_id}")
                continue
            
            logger.info(
                f"执行子 Agent",
                extra={
                    "agent_id": agent_id,
                    "agent_name": sub_agent_info.get("name"),
                    "priority": sub_agent_info.get("priority")
                }
            )
            
            # 执行子 Agent
            result = await self._execute_sub_agent(
                agent_data["config"],
                message,
                context,
                conversation_id,
                user_id,
                web_search,
                memory,
                storage_type,
                user_rag_memory_id
            )
            
            results.append({
                "agent_id": agent_id,
                "agent_name": sub_agent_info.get("name"),
                "result": result,
                "conversation_id": result.get("conversation_id")  # 保存会话 ID
            })
            
            # 更新上下文（后续 Agent 可以使用前面的结果）
            context[f"result_from_{sub_agent_info.get('name', agent_id)}"] = result.get("message")
        
        return results
    
    async def _execute_parallel(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> List[Dict[str, Any]]:
        """并行执行子 Agent
        
        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Returns:
            执行结果列表
        """
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")
        
        # 获取并发限制
        parallel_limit = self.config.execution_config.get("parallel_limit", 3)
        
        # 创建任务列表
        tasks = []
        for sub_agent_info in task_analysis["sub_agents"]:
            agent_id = sub_agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)
            
            if not agent_data:
                continue
            
            task = self._execute_sub_agent(
                agent_data["config"],
                message,
                context,
                conversation_id,
                user_id,
                web_search,
                memory,
                storage_type,
                user_rag_memory_id
            )
            tasks.append((agent_id, sub_agent_info.get("name"), task))
        
        # 并行执行（带限制）
        results = []
        for i in range(0, len(tasks), parallel_limit):
            batch = tasks[i:i + parallel_limit]
            batch_results = await asyncio.gather(
                *[task for _, _, task in batch],
                return_exceptions=True
            )
            
            for (agent_id, agent_name, _), result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.error(f"子 Agent 执行失败: {agent_name}", extra={"error": str(result)})
                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "error": str(result)
                    })
                else:
                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "result": result,
                        "conversation_id": result.get("conversation_id")  # 保存会话 ID
                    })
        
        return results
    
    async def _execute_conditional_stream(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ):
        """条件路由执行（流式）
        
        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Yields:
            SSE 格式的事件流
        """
        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)
        
        message = task_analysis.get("message", "")
        
        # 使用路由规则选择 Agent
        use_llm = task_analysis.get("use_llm_routing", True)
        selected_agent_info = await self._route_by_rules(
            message, 
            task_analysis["sub_agents"], 
            use_llm=use_llm,
            conversation_id=str(conversation_id) if conversation_id else None
        )
        
        if not selected_agent_info:
            selected_agent_info = task_analysis["sub_agents"][0]
            logger.info("未匹配到路由规则，使用默认 Agent")
        
        agent_id = selected_agent_info["agent_id"]
        agent_data = self.sub_agents.get(agent_id)
        
        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)
        
        logger.info(
            f"条件路由选择 Agent（流式）",
            extra={
                "agent_id": agent_id,
                "agent_name": selected_agent_info.get("name"),
                "message_preview": message[:50]
            }
        )
        
        # 发送路由信息事件
        yield self._format_sse_event("agent_selected", {
            "agent_id": agent_id,
            "agent_name": selected_agent_info.get("name")
        })
        
        # 流式执行子 Agent
        sub_conversation_id = None
        async for event in self._execute_sub_agent_stream(
            agent_data["config"],
            message,
            task_analysis.get("initial_context", {}),
            conversation_id,
            user_id,
            web_search,
            memory,
            storage_type,
            user_rag_memory_id
        ):
            # 解析事件以提取 conversation_id
            if "data:" in event:
                try:
                    import json
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    if "conversation_id" in data:
                        sub_conversation_id = data["conversation_id"]
                except:
                    pass
            
            yield event
        
        # 如果有会话 ID，发送一个包含它的事件
        if sub_conversation_id:
            yield self._format_sse_event("conversation", {
                "conversation_id": sub_conversation_id
            })
    
    async def _execute_conditional(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> Dict[str, Any]:
        """条件路由执行 - 根据路由规则选择合适的 Agent
        
        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Returns:
            执行结果
        """
        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)
        
        message = task_analysis.get("message", "")
        
        # 使用路由规则选择 Agent（默认启用 LLM）
        use_llm = task_analysis.get("use_llm_routing", True)
        selected_agent_info = await self._route_by_rules(
            message, 
            task_analysis["sub_agents"], 
            use_llm=use_llm,
            conversation_id=str(conversation_id) if conversation_id else None
        )
        
        if not selected_agent_info:
            # 如果没有匹配的规则，使用第一个 Agent
            selected_agent_info = task_analysis["sub_agents"][0]
            logger.info("未匹配到路由规则，使用默认 Agent")
        
        agent_id = selected_agent_info["agent_id"]
        agent_data = self.sub_agents.get(agent_id)
        
        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)
        
        logger.info(
            f"条件路由选择 Agent",
            extra={
                "agent_id": agent_id,
                "agent_name": selected_agent_info.get("name"),
                "message_preview": message[:50]
            }
        )
        
        result = await self._execute_sub_agent(
            agent_data["config"],
            message,
            task_analysis.get("initial_context", {}),
            conversation_id,
            user_id,
            web_search,
            memory,
            storage_type,
            user_rag_memory_id
        )
        
        # 确保返回子 Agent 的 conversation_id
        return {
            "agent_id": agent_id,
            "agent_name": selected_agent_info.get("name"),
            "result": result,
            "conversation_id": result.get("conversation_id")  # 传递子 Agent 的会话 ID
        }
    
    async def _route_by_rules(
        self, 
        message: str, 
        sub_agents: List[Dict[str, Any]],
        use_llm: bool = True,
        conversation_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """根据路由规则选择 Agent（支持 LLM 增强）
        
        Args:
            message: 用户消息
            sub_agents: 子 Agent 列表
            use_llm: 是否使用 LLM 辅助路由
            conversation_id: 会话 ID（用于多轮对话状态管理）
            
        Returns:
            选中的 Agent 信息，如果没有匹配则返回 None
        """
        # 如果配置了 LLM 路由器，优先使用
        if self.llm_router and use_llm:
            try:
                logger.info("使用 LLM 路由器进行智能路由")
                routing_result = await self.llm_router.route(
                    message=message,
                    conversation_id=conversation_id,
                    force_new=False
                )
                
                selected_agent_id = routing_result["agent_id"]
                confidence = routing_result["confidence"]
                method = routing_result.get("routing_method", "unknown")
                
                logger.info(
                    f"LLM 路由完成",
                    extra={
                        "agent_id": selected_agent_id,
                        "confidence": confidence,
                        "method": method,
                        "strategy": routing_result.get("strategy"),
                        "topic": routing_result.get("topic")
                    }
                )
                
                # 查找对应的 Agent
                for agent in sub_agents:
                    if agent["agent_id"] == selected_agent_id:
                        return agent
                
                logger.warning(f"LLM 路由返回的 agent_id 不在子 Agent 列表中: {selected_agent_id}")
                
            except Exception as e:
                logger.error(f"LLM 路由失败，降级到关键词路由: {str(e)}")
        
        # 降级到关键词路由
        if not self.config.routing_rules:
            return None
        
        message_lower = message.lower()
        best_match = None
        best_score = 0
        
        # 关键词匹配
        for rule in self.config.routing_rules:
            target_agent_id = rule.get("target_agent_id")
            condition = rule.get("condition", "")
            priority = rule.get("priority", 1)
            
            # 解析条件表达式（简化版本：支持 contains_any）
            score = self._evaluate_condition(condition, message_lower)
            
            # 考虑优先级
            weighted_score = score * priority
            
            if weighted_score > best_score:
                # 找到对应的 Agent
                for agent in sub_agents:
                    if agent["agent_id"] == target_agent_id:
                        best_match = agent
                        best_score = weighted_score
                        break
        
        if best_match:
            logger.info(
                f"关键词路由",
                extra={
                    "agent_name": best_match.get("name"),
                    "score": best_score
                }
            )
        
        return best_match
    

    def _evaluate_condition(self, condition: str, message: str) -> float:
        """评估条件表达式
        
        Args:
            condition: 条件表达式，如 "contains_any(['数学', '物理'])"
            message: 消息文本（已转小写）
            
        Returns:
            匹配分数 (0-1)
        """
        import re
        
        # 解析 contains_any(['keyword1', 'keyword2', ...])
        match = re.search(r"contains_any\(\[(.*?)\]\)", condition)
        if not match:
            return 0
        
        # 提取关键词列表
        keywords_str = match.group(1)
        keywords = [k.strip().strip("'\"") for k in keywords_str.split(",")]
        
        # 计算匹配分数
        matched_count = 0
        for keyword in keywords:
            if keyword.lower() in message:
                matched_count += 1
        
        if not keywords:
            return 0
        
        # 返回匹配比例
        return matched_count / len(keywords)
    
    async def _execute_loop(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> Dict[str, Any]:
        """循环执行（迭代优化）
        
        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Returns:
            执行结果
        """
        max_iterations = self.config.execution_config.get("max_iterations", 5)
        
        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)
        
        agent_info = task_analysis["sub_agents"][0]
        agent_id = agent_info["agent_id"]
        agent_data = self.sub_agents.get(agent_id)
        
        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)
        
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")
        
        result = None
        for i in range(max_iterations):
            logger.info(
                f"循环执行 Agent",
                extra={
                    "iteration": i + 1,
                    "max_iterations": max_iterations,
                    "agent_name": agent_info.get("name")
                }
            )
            
            result = await self._execute_sub_agent(
                agent_data["config"],
                message,
                context,
                conversation_id,
                user_id,
                web_search,
                memory,
                storage_type,
                user_rag_memory_id
            )
            
            # 简化版本：执行一次就返回
            # 在实际应用中，应该验证结果是否满足条件
            break
        
        return {
            "agent_id": agent_id,
            "agent_name": agent_info.get("name"),
            "iterations": i + 1,
            "result": result,
            "conversation_id": result.get("conversation_id") if result else None  # 保存会话 ID
        }
    
    async def _execute_sub_agent_stream(
        self,
        agent_config: AgentConfig,
        message: str,
        context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ):
        """执行单个子 Agent（流式）
        
        Args:
            agent_config: Agent 配置
            message: 消息
            context: 上下文
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Yields:
            SSE 格式的事件流
        """
        from app.services.draft_run_service import DraftRunService
        
        # 获取模型配置
        model_config = self.db.get(ModelConfig, agent_config.default_model_config_id)
        if not model_config:
            raise BusinessException(
                f"Agent 模型配置不存在",
                BizCode.AGENT_CONFIG_MISSING
            )
        
        # 流式执行 Agent
        draft_service = DraftRunService(self.db)
        async for event in draft_service.run_stream(
            agent_config=agent_config,
            model_config=model_config,
            message=message,
            workspace_id=agent_config.app.workspace_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_id=user_id,
            variables=context,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            web_search=web_search,
            memory=memory
        ):
            yield event
    
    async def _execute_sub_agent(
        self,
        agent_config: AgentConfig,
        message: str,
        context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> Dict[str, Any]:
        """执行单个子 Agent
        
        Args:
            agent_config: Agent 配置
            message: 消息
            context: 上下文
            conversation_id: 会话 ID
            user_id: 用户 ID
            
        Returns:
            执行结果
        """
        from app.services.draft_run_service import DraftRunService
        
        # 获取模型配置
        model_config = self.db.get(ModelConfig, agent_config.default_model_config_id)
        if not model_config:
            raise BusinessException(
                f"Agent 模型配置不存在",
                BizCode.AGENT_CONFIG_MISSING
            )
        
        # 执行 Agent
        draft_service = DraftRunService(self.db)
        result = await draft_service.run(
            agent_config=agent_config,
            model_config=model_config,
            message=message,
            workspace_id=agent_config.app.workspace_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_id=user_id,
            variables=context,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        )
        
        return result
    
    async def _aggregate_results(
        self,
        results: Any
    ) -> str:
        """整合子 Agent 的结果
        
        Args:
            results: 子 Agent 执行结果
            
        Returns:
            整合后的结果
        """
        strategy = self.config.aggregation_strategy
        
        if strategy == "merge":
            return self._merge_results(results)
        elif strategy == "vote":
            return self._vote_results(results)
        elif strategy == "priority":
            return self._priority_results(results)
        else:
            return self._merge_results(results)
    
    def _merge_results(self, results: Any) -> str:
        """合并所有结果
        
        Args:
            results: 执行结果
            
        Returns:
            合并后的结果
        """
        if isinstance(results, list):
            # 顺序或并行执行的结果
            merged = []
            for item in results:
                if "result" in item:
                    agent_name = item.get("agent_name", "Agent")
                    message = item["result"].get("message", "")
                    merged.append(f"【{agent_name}】\n{message}")
                elif "error" in item:
                    agent_name = item.get("agent_name", "Agent")
                    merged.append(f"【{agent_name}】\n错误: {item['error']}")
            
            return "\n\n".join(merged)
        elif isinstance(results, dict):
            # 条件或循环执行的结果
            if "result" in results:
                return results["result"].get("message", "")
            return str(results)
        
        return str(results)
    
    def _vote_results(self, results: Any) -> str:
        """投票选择最佳结果（简化版本）
        
        Args:
            results: 执行结果
            
        Returns:
            最佳结果
        """
        # 简化版本：返回第一个成功的结果
        if isinstance(results, list):
            for item in results:
                if "result" in item:
                    return item["result"].get("message", "")
        
        return self._merge_results(results)
    
    def _priority_results(self, results: Any) -> str:
        """按优先级选择结果（简化版本）
        
        Args:
            results: 执行结果
            
        Returns:
            优先级最高的结果
        """
        # 简化版本：返回第一个结果
        if isinstance(results, list) and results:
            if "result" in results[0]:
                return results[0]["result"].get("message", "")
        
        return self._merge_results(results)
    
    def _format_sse_event(self, event: str, data: Dict[str, Any]) -> str:
        """格式化 SSE 事件
        
        Args:
            event: 事件类型
            data: 事件数据
            
        Returns:
            SSE 格式的字符串
        """
        import json
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    
    def _load_agent(self, release_id: uuid.UUID):
        """从发布版本加载 Agent 配置
        
        Args:
            release_id: 发布版本 ID
            
        Returns:
            Agent 配置对象（包含发布版本的配置数据）
        """
        from app.models import AppRelease, App
        
        # 获取发布版本
        release = self.db.get(AppRelease, release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(release_id))
        
        # 从发布版本的 config 中获取 Agent 配置
        config_data = release.config
        if not config_data:
            raise BusinessException(f"发布版本 {release_id} 缺少配置数据", BizCode.AGENT_CONFIG_MISSING)
        
        # 获取应用信息（用于 workspace_id）
        app = self.db.get(App, release.app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(release.app_id))
        
        # 创建一个类似 AgentConfig 的对象，包含所有需要的属性
        class AgentConfigProxy:
            """Agent 配置代理对象，模拟 AgentConfig 的接口"""
            def __init__(self, release, app, config_data):
                self.id = release.id
                self.app_id = release.app_id
                self.app = app
                self.name = release.name
                self.description = release.description
                self.system_prompt = config_data.get("system_prompt")
                self.model_parameters = config_data.get("model_parameters")
                self.knowledge_retrieval = config_data.get("knowledge_retrieval")
                self.memory = config_data.get("memory")
                self.variables = config_data.get("variables", [])
                self.tools = config_data.get("tools", {})
                self.default_model_config_id = release.default_model_config_id
        
        return AgentConfigProxy(release, app, config_data)
