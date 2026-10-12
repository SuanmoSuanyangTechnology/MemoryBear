"""多 Agent 控制器"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.core.response_utils import success
from app.core.logging_config import get_business_logger
from app.schemas import MultiAgentConfigUpdate, MultiAgentConfigSchema
from app.schemas.multi_agent_schema import MultiAgentRunRequest
from app.services.multi_agent_service import MultiAgentService, get_multi_agent_service
from app.services.multi_agent_handoffs_integration import MultiAgentHandoffsService
from app.models import User

router = APIRouter(prefix="/apps", tags=["Multi-Agent"])
logger = get_business_logger()


# ==================== 多 Agent 配置管理 ====================


@router.get(
    "/{app_id}/multi-agent",
    summary="获取当前应用的最新有效多 Agent 配置"
)
def get_multi_agent_configs(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取指定应用的最新有效多 Agent 配置，如果不存在则返回默认模板"""
    service = MultiAgentService(db)

    # 通过 app_id 获取最新有效配置（已转换 agent_id 为 app_id）
    config = service.get_multi_agent_configs(app_id)

    if not config:
        # 返回默认模板
        default_template = {
            "app_id": str(app_id),
            "default_model_config_id": None,
            "model_parameters": None,
            "orchestration_mode": "supervisor_loop",
            "sub_agents": [],
            "routing_rules": [],
            "execution_config": {
                # 对齐 ExecutionConfig schema 默认值。执行护栏（高级）已下线前端配置，
                # timeout / stream_idle_timeout / parallel_limit / retry_on_failure /
                # max_retries 不在此暴露，统一由后端 schema 默认值生效
                "max_iterations": 10
            },
            # 主管即 Agent：默认裸主管（None = 硬编码 prompt + 仅 SubAgentTool）
            "supervisor_config": None,
            # S5 契约：默认未声明集群变量（None = 运行时退化为子 Agent 变量并集）
            "variables": None,
            "aggregation_strategy": "merge",
        }
        return success(
            data=default_template,
            msg="该应用暂无配置，返回默认模板"
        )

    # config 已经是字典格式，直接返回
    return success(data=config)

@router.put(
    "/{app_id}/multi-agent",
    summary="更新多 Agent 配置"
)
def update_multi_agent_config(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    data: MultiAgentConfigUpdate = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    multi_agent_service: Annotated[MultiAgentService, Depends(get_multi_agent_service)] = None,
):
    """更新多 Agent 配置"""
    config = multi_agent_service.update_config(app_id, data)
    return success(
        data=MultiAgentConfigSchema.model_validate(config),
        msg="多 Agent 配置更新成功"
    )


# ==================== Agent Handoffs 协作 ====================

@router.post(
    "/{app_id}/chat/handoffs",
    summary="支持 Agent Handoffs 的聊天接口"
)
async def chat_with_handoffs(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: MultiAgentRunRequest = ...,
    current_user: User = Depends(get_current_user),
    multi_agent_service: Annotated[MultiAgentService, Depends(get_multi_agent_service)] = None,
    db: Session = Depends(get_db),
):
    """支持 Agent Handoffs 的聊天接口
    
    基于 LangChain handoffs 模式，支持：
    - Agent 之间的动态切换
    - 工具驱动的状态转换
    - 会话上下文的保持
    - 协作历史的追踪
    
    配置要求：
    在 execution_config 中设置 "enable_handoffs": true
    
    返回信息包括：
    - message: 最终回复
    - conversation_id: 会话 ID
    - final_agent_id: 最终处理的 Agent
    - handoff_count: 切换次数
    - handoff_history: 切换历史记录
    """
    # 创建 handoffs 服务
    handoffs_service = MultiAgentHandoffsService(db, multi_agent_service)
    
    # 执行协作
    result = await handoffs_service.run_with_handoffs(app_id, request)
    
    return success(
        data=result,
        msg="Agent Handoffs 执行成功"
    )


@router.post(
    "/{app_id}/chat/handoffs/stream",
    summary="流式 Agent Handoffs 聊天接口"
)
async def chat_with_handoffs_stream(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: MultiAgentRunRequest = ...,
    current_user: User = Depends(get_current_user),
    multi_agent_service: Annotated[MultiAgentService, Depends(get_multi_agent_service)] = None,
    db: Session = Depends(get_db),
):
    """流式 Agent Handoffs 聊天接口
    
    以 SSE (Server-Sent Events) 格式返回流式响应
    
    事件类型：
    - start: 开始执行
    - message: Agent 消息
    - handoff: Agent 切换事件
    - end: 执行结束
    - error: 错误信息
    """
    # 创建 handoffs 服务
    handoffs_service = MultiAgentHandoffsService(db, multi_agent_service)
    
    # 流式执行
    return StreamingResponse(
        handoffs_service.run_stream_with_handoffs(app_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get(
    "/conversations/{conversation_id}/handoffs/history",
    summary="获取会话的 Handoff 历史"
)
def get_handoff_history(
    conversation_id: str = Path(..., description="会话 ID"),
    current_user: User = Depends(get_current_user),
    multi_agent_service: Annotated[MultiAgentService, Depends(get_multi_agent_service)] = None,
    db: Session = Depends(get_db),
):
    """获取指定会话的 Agent Handoff 历史记录
    
    返回信息包括：
    - conversation_id: 会话 ID
    - current_agent_id: 当前活跃的 Agent
    - handoff_count: 总切换次数
    - handoff_history: 详细的切换历史
    """
    # 创建 handoffs 服务
    handoffs_service = MultiAgentHandoffsService(db, multi_agent_service)
    
    # 获取历史
    history = handoffs_service.get_handoff_history(conversation_id)
    
    if not history:
        return success(
            data=None,
            msg="该会话没有 Handoff 历史记录"
        )
    
    return success(
        data=history,
        msg="获取 Handoff 历史成功"
    )


@router.delete(
    "/conversations/{conversation_id}/handoffs",
    summary="清除会话的 Handoff 状态"
)
def clear_handoff_state(
    conversation_id: str = Path(..., description="会话 ID"),
    current_user: User = Depends(get_current_user),
    multi_agent_service: Annotated[MultiAgentService, Depends(get_multi_agent_service)] = None,
    db: Session = Depends(get_db),
):
    """清除指定会话的 Handoff 状态
    
    用于：
    - 重置会话状态
    - 清理测试数据
    - 释放内存
    """
    # 创建 handoffs 服务
    handoffs_service = MultiAgentHandoffsService(db, multi_agent_service)
    
    # 清除状态
    handoffs_service.clear_handoff_state(conversation_id)
    
    return success(msg="Handoff 状态已清除")


@router.post(
    "/{app_id}/handoffs/test-routing",
    summary="测试 Handoff 路由决策"
)
async def test_handoff_routing(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    message: str = ...,
    current_user: User = Depends(get_current_user),
    multi_agent_service: Annotated[MultiAgentService, Depends(get_multi_agent_service)] = None,
    db: Session = Depends(get_db),
):
    """测试 Handoff 路由决策（不实际执行）
    
    用于调试和测试路由逻辑，返回：
    - initial_agent_id: 初始选择的 Agent
    - available_handoff_tools: 可用的切换工具列表
    - handoff_suggestion: 自动切换建议
    
    不会实际执行 Agent，只返回路由决策信息
    """
    # 创建 handoffs 服务
    handoffs_service = MultiAgentHandoffsService(db, multi_agent_service)
    
    # 测试路由
    result = await handoffs_service.test_handoff_routing(app_id, message)
    
    return success(
        data=result,
        msg="路由测试完成"
    )
