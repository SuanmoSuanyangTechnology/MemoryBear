"""Agent 发现和调用工具"""
import uuid
import time
import datetime
from typing import Optional, Dict, Any, List
from langchain.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import AgentConfig, ModelConfig, AgentInvocation
from app.services.agent_registry import AgentRegistry
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.repositories import workspace_repository, knowledge_repository

logger = get_business_logger()


# ==================== Agent 发现工具 ====================

class AgentDiscoveryInput(BaseModel):
    """Agent 发现工具输入参数"""
    query: Optional[str] = Field(None, description="搜索关键词，如：'客服'、'技术支持'")
    domain: Optional[str] = Field(None, description="专业领域，如：'customer_service'、'technical_support'")
    capabilities: Optional[List[str]] = Field(None, description="所需能力列表，如：['退货处理', '订单查询']")


def create_agent_discovery_tool(registry: AgentRegistry, workspace_id: uuid.UUID):
    """创建 Agent 发现工具
    
    Args:
        registry: Agent 注册表
        workspace_id: 当前工作空间 ID
        
    Returns:
        Agent 发现工具
    """
    
    @tool(args_schema=AgentDiscoveryInput)
    def discover_agents(
        query: Optional[str] = None,
        domain: Optional[str] = None,
        capabilities: Optional[List[str]] = None
    ) -> str:
        """发现系统中可用的 Agent。当需要找到能够处理特定任务的 Agent 时使用此工具。
        
        Args:
            query: 搜索关键词（如："客服"、"技术支持"）
            domain: 专业领域（如："customer_service"、"technical_support"）
            capabilities: 所需能力（如：["退货处理", "订单查询"]）
            
        Returns:
            可用 Agent 的列表和描述
        """
        try:
            agents = registry.discover_agents(
                query=query,
                domain=domain,
                capabilities=capabilities,
                workspace_id=workspace_id
            )
            
            if not agents:
                return "未找到匹配的 Agent"
            
            # 格式化输出
            result = f"找到 {len(agents)} 个可用的 Agent：\n\n"
            for i, agent in enumerate(agents, 1):
                result += f"{i}. {agent['name']}\n"
                result += f"   ID: {agent['id']}\n"
                if agent['description']:
                    result += f"   描述: {agent['description']}\n"
                if agent['domain']:
                    result += f"   领域: {agent['domain']}\n"
                if agent['capabilities']:
                    result += f"   能力: {', '.join(agent['capabilities'])}\n"
                if agent['tools']:
                    result += f"   工具: {', '.join(agent['tools'])}\n"
                result += "\n"
            
            logger.info(
                "Agent 发现成功",
                extra={
                    "query": query,
                    "domain": domain,
                    "found_count": len(agents)
                }
            )
            
            return result
            
        except Exception as e:
            logger.error("Agent 发现失败", extra={"error": str(e)})
            return f"发现 Agent 失败: {str(e)}"
    
    return discover_agents


# ==================== Agent 调用工具 ====================

class AgentInvocationInput(BaseModel):
    """Agent 调用工具输入参数"""
    agent_id: str = Field(..., description="要调用的 Agent ID（通过 discover_agents 工具获取）")
    message: str = Field(..., description="发送给 Agent 的消息或任务描述")
    context: Optional[Dict[str, Any]] = Field(None, description="可选的上下文信息（如：用户信息、历史记录等）")


def create_agent_invocation_tool(
    db: Session,
    registry: AgentRegistry,
    workspace_id: uuid.UUID,
    current_agent_id: uuid.UUID,
    conversation_id: Optional[uuid.UUID] = None,
    parent_invocation_id: Optional[uuid.UUID] = None,
    invocation_chain: Optional[List[uuid.UUID]] = None
):
    """创建 Agent 调用工具
    
    Args:
        db: 数据库会话
        registry: Agent 注册表
        workspace_id: 当前工作空间 ID
        current_agent_id: 当前 Agent ID
        conversation_id: 会话 ID
        parent_invocation_id: 父调用 ID
        invocation_chain: 调用链（用于检测循环调用）
        
    Returns:
        Agent 调用工具
    """
    # 1. 获取工作空间的 storage_type
    storage_type = 'neo4j'  # 默认值
    user_rag_memory_id = None
    
    try:
        workspace = workspace_repository.get_workspace_by_id(db, workspace_id)
        if workspace and workspace.storage_type:
            storage_type = workspace.storage_type
            logger.debug(
                "获取工作空间存储类型成功",
                extra={
                    "workspace_id": str(workspace_id),
                    "storage_type": storage_type
                }
            )
    except Exception as e:
        logger.warning(
            "获取工作空间存储类型失败，使用默认值 neo4j",
            extra={"workspace_id": str(workspace_id), "error": str(e)}
        )
    
    # 2. 如果 storage_type 是 rag，获取知识库 ID
    if storage_type == 'rag':
        try:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db,
                name="USER_RAG_MEMORY",
                workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)
                logger.debug(
                    "获取 RAG 知识库成功",
                    extra={
                        "workspace_id": str(workspace_id),
                        "knowledge_id": user_rag_memory_id
                    }
                )
            else:
                logger.warning(
                    "未找到名为 'USER_RAG_MEMORY' 的知识库，将使用 neo4j 存储",
                    extra={"workspace_id": str(workspace_id)}
                )
                storage_type = 'neo4j'
        except Exception as e:
            logger.warning(
                "获取 RAG 知识库失败，将使用 neo4j 存储",
                extra={"workspace_id": str(workspace_id), "error": str(e)}
            )
            storage_type = 'neo4j'
    
    if invocation_chain is None:
        invocation_chain = []
    
    @tool(args_schema=AgentInvocationInput)
    async def invoke_agent(
        agent_id: str,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """调用另一个 Agent 来处理任务。当当前 Agent 无法处理某个任务，需要其他专业 Agent 帮助时使用。
        
        Args:
            agent_id: 要调用的 Agent ID（通过 discover_agents 工具获取）
            message: 发送给 Agent 的消息或任务描述
            context: 可选的上下文信息（如：用户信息、历史记录等）
            
        Returns:
            被调用 Agent 的响应结果
        """
        try:
            # 1. 验证 Agent 存在
            agent_uuid = uuid.UUID(agent_id)
            agent_info = registry.get_agent(agent_uuid)
            if not agent_info:
                return f"Agent {agent_id} 不存在"
            
            # 2. 验证权限（同工作空间或公开）
            if agent_info["workspace_id"] != str(workspace_id) and agent_info["visibility"] != "public":
                return f"无权访问 Agent {agent_info['name']}"
            
            # 3. 防止自己调用自己
            if agent_id == str(current_agent_id):
                return "不能调用自己"
            
            # 4. 防止循环调用
            if agent_uuid in invocation_chain:
                return f"检测到循环调用：{agent_info['name']} 已在调用链中"
            
            # 5. 检查调用深度
            max_depth = 5
            if len(invocation_chain) >= max_depth:
                return f"调用深度超过限制（最大 {max_depth} 层）"
            
            # 6. 获取 Agent 配置
            agent_config = db.get(AgentConfig, agent_uuid)
            if not agent_config:
                return "Agent 配置不存在"
            
            # 7. 获取模型配置
            model_config = db.get(ModelConfig, agent_config.default_model_config_id)
            if not model_config:
                return "Agent 模型配置不存在"
            
            # 8. 创建调用记录
            invocation = AgentInvocation(
                caller_agent_id=current_agent_id,
                callee_agent_id=agent_uuid,
                conversation_id=conversation_id,
                parent_invocation_id=parent_invocation_id,
                input_message=message,
                context=context,
                status="running",
                started_at=datetime.datetime.now()
            )
            db.add(invocation)
            db.commit()
            db.refresh(invocation)
            
            logger.info(
                "Agent 调用开始",
                extra={
                    "invocation_id": str(invocation.id),
                    "caller_agent_id": str(current_agent_id),
                    "callee_agent_id": agent_id,
                    "depth": len(invocation_chain)
                }
            )
            
            start_time = time.time()
            
            try:
                # 9. 调用 Agent
                from app.services.draft_run_service import DraftRunService
                draft_service = DraftRunService(db)
                
                result = await draft_service.run(
                    agent_config=agent_config,
                    model_config=model_config,
                    message=message,
                    workspace_id=workspace_id,
                    variables=context or {},
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id
                )
                
                elapsed_time = time.time() - start_time
                
                # 10. 更新调用记录
                invocation.status = "completed"
                invocation.output_message = result["message"]
                invocation.completed_at = datetime.datetime.now()
                invocation.elapsed_time = elapsed_time
                invocation.token_usage = result.get("usage", {})
                db.commit()
                
                logger.info(
                    "Agent 调用成功",
                    extra={
                        "invocation_id": str(invocation.id),
                        "caller_agent_id": str(current_agent_id),
                        "callee_agent_id": agent_id,
                        "elapsed_time": elapsed_time
                    }
                )
                
                return result["message"]
                
            except Exception as e:
                # 更新调用记录为失败
                invocation.status = "failed"
                invocation.error_message = str(e)
                invocation.completed_at = datetime.datetime.now()
                invocation.elapsed_time = time.time() - start_time
                db.commit()
                
                logger.error(
                    "Agent 调用失败",
                    extra={
                        "invocation_id": str(invocation.id),
                        "caller_agent_id": str(current_agent_id),
                        "callee_agent_id": agent_id,
                        "error": str(e)
                    }
                )
                
                raise
            
        except Exception as e:
            logger.error(
                "Agent 调用异常",
                extra={
                    "caller_agent_id": str(current_agent_id),
                    "callee_agent_id": agent_id,
                    "error": str(e)
                }
            )
            return f"调用 Agent 失败: {str(e)}"
    
    return invoke_agent
