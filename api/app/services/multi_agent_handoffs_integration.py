"""Multi-Agent Service 的 Handoffs 集成

将 Agent Handoffs 功能集成到现有的 Multi-Agent 系统中
"""
import uuid
import time
from typing import Dict, Any, Optional, AsyncGenerator
from sqlalchemy.orm import Session

from app.services.agent_handoff import get_handoff_manager
from app.services.collaborative_orchestrator import CollaborativeOrchestrator
from app.schemas.multi_agent_schema import MultiAgentRunRequest
from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.services.multi_agent_service import MultiAgentService

logger = get_business_logger()


class MultiAgentHandoffsService:
    """Multi-Agent Handoffs 服务 - 扩展现有的 Multi-Agent Service"""

    def __init__(self, db: Session, multi_agent_service:MultiAgentService):
        """初始化服务

        Args:
            db: 数据库会话
            multi_agent_service: 现有的 MultiAgentService 实例
        """
        self.db = db
        self.multi_agent_service = multi_agent_service
        self.handoff_manager = get_handoff_manager()

        logger.info("Multi-Agent Handoffs 服务初始化完成")

    async def run_with_handoffs(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest
    ) -> Dict[str, Any]:
        """运行支持 handoffs 的多 Agent 任务

        Args:
            app_id: 应用 ID
            request: 运行请求

        Returns:
            执行结果
        """
        start_time = time.time()

        try:
            # 1. 获取配置
            config = self.multi_agent_service.get_config(app_id)
            if not config:
                raise BusinessException(
                    "多 Agent 配置不存在",
                    BizCode.RESOURCE_NOT_FOUND
                )

            # 2. 检查是否启用 handoffs
            execution_config = config.execution_config or {}
            print("="*50)
            print(execution_config)
            enable_handoffs = execution_config.get("enable_handoffs", False)

            if not enable_handoffs:
                # 降级到普通模式
                logger.info("Handoffs 未启用，使用普通模式")
                return await self.multi_agent_service.run(app_id, request)

            # 3. 创建协作编排器
            orchestrator = CollaborativeOrchestrator(
                db=self.db,
                config=config,
                handoff_manager=self.handoff_manager
            )

            # 4. 执行协作
            result = await orchestrator.execute_with_handoffs(
                message=request.message,
                conversation_id=str(request.conversation_id) if request.conversation_id else None,
                user_id=request.user_id,
                variables=request.variables
            )

            # 5. 增强结果
            result["mode"] = "handoffs"
            result["elapsed_time"] = time.time() - start_time

            logger.info(
                "Handoffs 执行完成",
                extra={
                    "app_id": str(app_id),
                    "handoff_count": result.get("handoff_count", 0),
                    "final_agent": result.get("final_agent_id"),
                    "elapsed_time": result["elapsed_time"]
                }
            )

            return result

        except Exception as e:
            logger.error(f"Handoffs 执行失败: {str(e)}")

            # 降级到普通模式
            logger.info("降级到普通模式")
            return await self.multi_agent_service.run(app_id, request)

    async def run_stream_with_handoffs(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest
    ) -> AsyncGenerator[str, None]:
        """流式运行支持 handoffs 的多 Agent 任务

        Args:
            app_id: 应用 ID
            request: 运行请求

        Yields:
            SSE 格式的事件流
        """
        try:
            # 1. 获取配置
            config = self.multi_agent_service.get_config(app_id)
            if not config:
                yield f"data: {{\"event\": \"error\", \"error\": \"配置不存在\"}}\n\n"
                return

            # 2. 检查是否启用 handoffs
            execution_config = config.execution_config or {}
            enable_handoffs = execution_config.get("enable_handoffs", False)

            if not enable_handoffs:
                # 降级到普通流式模式
                async for event in self.multi_agent_service.run_stream(app_id, request):
                    yield event
                return

            # 3. 创建协作编排器
            orchestrator = CollaborativeOrchestrator(
                db=self.db,
                config=config,
                handoff_manager=self.handoff_manager
            )

            # 4. 流式执行
            async for event in orchestrator.execute_stream_with_handoffs(
                message=request.message,
                conversation_id=str(request.conversation_id) if request.conversation_id else None,
                user_id=request.user_id,
                variables=request.variables
            ):
                yield event

        except Exception as e:
            logger.error(f"流式 Handoffs 执行失败: {str(e)}")
            yield f"data: {{\"event\": \"error\", \"error\": \"{str(e)}\"}}\n\n"

    def get_handoff_history(
        self,
        conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        """获取会话的 handoff 历史

        Args:
            conversation_id: 会话 ID

        Returns:
            Handoff 历史信息
        """
        state = self.handoff_manager.get_state(conversation_id)
        if not state:
            return None

        return {
            "conversation_id": state.conversation_id,
            "current_agent_id": state.current_agent_id,
            "handoff_count": state.get_handoff_count(),
            "handoff_history": [
                {
                    "from_agent": h.from_agent_id,
                    "to_agent": h.to_agent_id,
                    "reason": h.reason,
                    "timestamp": h.timestamp.isoformat(),
                    "user_message": h.user_message,
                    "context_summary": h.context_summary
                }
                for h in state.handoff_history
            ],
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat()
        }

    def clear_handoff_state(self, conversation_id: str):
        """清除会话的 handoff 状态

        Args:
            conversation_id: 会话 ID
        """
        self.handoff_manager.clear_state(conversation_id)
        logger.info(f"清除 handoff 状态: {conversation_id}")

    async def test_handoff_routing(
        self,
        app_id: uuid.UUID,
        message: str
    ) -> Dict[str, Any]:
        """测试 handoff 路由决策（不实际执行）

        Args:
            app_id: 应用 ID
            message: 测试消息

        Returns:
            路由决策结果
        """
        # 1. 获取配置
        config = self.multi_agent_service.get_config(app_id)
        if not config:
            raise BusinessException(
                "多 Agent 配置不存在",
                BizCode.RESOURCE_NOT_FOUND
            )

        # 2. 解析 sub agents
        sub_agents = {}
        for agent_data in config.sub_agents:
            agent_id = agent_data.get("agent_id")
            if agent_id:
                sub_agents[str(agent_id)] = {
                    "info": agent_data
                }

        # 3. 测试路由
        test_conversation_id = f"test-{uuid.uuid4()}"

        # 选择初始 Agent
        initial_agent_id = None
        message_lower = message.lower()

        for agent_id, agent_data in sub_agents.items():
            agent_info = agent_data.get("info", {})
            capabilities = agent_info.get("capabilities", [])
            role = agent_info.get("role", "")

            keywords = capabilities + ([role] if role else [])
            for keyword in keywords:
                if keyword.lower() in message_lower:
                    initial_agent_id = agent_id
                    break

            if initial_agent_id:
                break

        if not initial_agent_id:
            initial_agent_id = next(iter(sub_agents.keys()))

        # 4. 生成 handoff 工具
        handoff_tools = self.handoff_manager.generate_handoff_tools(
            initial_agent_id,
            sub_agents
        )

        # 5. 检查是否需要 handoff
        handoff_suggestion = self.handoff_manager.should_handoff(
            conversation_id=test_conversation_id,
            current_agent_id=initial_agent_id,
            message=message,
            available_agents=sub_agents
        )

        return {
            "message": message,
            "initial_agent_id": initial_agent_id,
            "initial_agent_name": sub_agents[initial_agent_id]["info"].get("name", ""),
            "available_handoff_tools": [
                {
                    "name": tool.name,
                    "target_agent_id": tool.target_agent_id,
                    "target_agent_name": tool.target_agent_name,
                    "description": tool.description
                }
                for tool in handoff_tools
            ],
            "handoff_suggestion": handoff_suggestion,
            "total_agents": len(sub_agents)
        }


# 使用示例
"""
from app.services.multi_agent_service import MultiAgentService
from app.services.multi_agent_handoffs_integration import MultiAgentHandoffsService

# 创建服务
multi_agent_service = MultiAgentService(db)
handoffs_service = MultiAgentHandoffsService(db, multi_agent_service)

# 运行 handoffs
result = await handoffs_service.run_with_handoffs(
    app_id=app_id,
    request=MultiAgentRunRequest(
        message="帮我解方程然后写诗",
        conversation_id=uuid.uuid4(),
        user_id="user-123"
    )
)

# 查看 handoff 历史
history = handoffs_service.get_handoff_history(str(result["conversation_id"]))
print(f"Handoff 次数: {history['handoff_count']}")
for h in history['handoff_history']:
    print(f"{h['from_agent']} → {h['to_agent']}: {h['reason']}")
"""
