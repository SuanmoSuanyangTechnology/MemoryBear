"""多 Agent 控制器"""
import uuid
from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.core.response_utils import success
from app.core.logging_config import get_business_logger
from app.schemas import multi_agent_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services.multi_agent_service import MultiAgentService
from app.models import User

router = APIRouter(prefix="/apps", tags=["Multi-Agent"])
logger = get_business_logger()


# ==================== 多 Agent 配置管理 ====================

@router.post(
    "/{app_id}/multi-agent",
    summary="创建多 Agent 配置"
)
def create_multi_agent_config(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    data: multi_agent_schema.MultiAgentConfigCreate = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建多 Agent 配置
    
    支持四种编排模式：
    - sequential: 顺序执行
    - parallel: 并行执行
    - conditional: 条件路由
    - loop: 循环执行
    """
    service = MultiAgentService(db)
    config = service.create_config(
        app_id=app_id,
        data=data,
        created_by=current_user.id
    )
    
    return success(
        data=multi_agent_schema.MultiAgentConfigSchema.model_validate(config),
        msg="多 Agent 配置创建成功"
    )



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
            "master_agent_id": None,
            "master_agent_name": None,
            "orchestration_mode": "conditional",
            "sub_agents": [],
            "routing_rules": [],
            "execution_config": {
                "max_iterations": 10,
                "timeout": 300,
                "enable_parallel": False,
                "error_handling": "stop"
            },
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
    data: multi_agent_schema.MultiAgentConfigUpdate = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新多 Agent 配置"""
    service = MultiAgentService(db)
    config = service.update_config(app_id, data)
    
    return success(
        data=multi_agent_schema.MultiAgentConfigSchema.model_validate(config),
        msg="多 Agent 配置更新成功"
    )


@router.delete(
    "/{app_id}/multi-agent",
    summary="删除多 Agent 配置"
)
def delete_multi_agent_config(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除多 Agent 配置"""
    service = MultiAgentService(db)
    service.delete_config(app_id)
    
    return success(msg="多 Agent 配置删除成功")

# ==================== 多 Agent 运行 ====================

@router.post(
    "/{app_id}/multi-agent/run",
    summary="运行多 Agent 任务"
)
async def run_multi_agent(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: multi_agent_schema.MultiAgentRunRequest = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """运行多 Agent 任务
    
    根据配置的编排模式执行多个 Agent：
    - sequential: 按优先级顺序执行
    - parallel: 并行执行所有 Agent
    - conditional: 根据条件选择 Agent
    - loop: 循环执行直到满足条件
    """
    service = MultiAgentService(db)
    result = await service.run(app_id, request)
    
    return success(
        data=multi_agent_schema.MultiAgentRunResponse(**result),
        msg="多 Agent 任务执行成功"
    )


# ==================== 智能路由测试 ====================

@router.post(
    "/{app_id}/multi-agent/test-routing",
    summary="测试智能路由（支持 Master Agent 模式）"
)
async def test_routing(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: multi_agent_schema.RoutingTestRequest = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试智能路由功能（重构版 - 支持 Master Agent）
    
    支持三种路由模式：
    - master_agent: 使用 Master Agent 决策（推荐）
    - llm_router: 使用旧 LLM 路由器（向后兼容）
    - rule_only: 仅使用规则路由（最快）
    
    参数：
    - message: 测试消息
    - conversation_id: 会话 ID（可选）
    - routing_model_id: 路由模型 ID（可选）
    - use_llm: 是否启用 LLM（默认 False）
    - keyword_threshold: 关键词置信度阈值（默认 0.8）
    - force_new: 是否强制重新路由（默认 False）
    """
    from app.services.conversation_state_manager import ConversationStateManager
    from app.services.llm_router import LLMRouter
    from app.models import ModelConfig
    
    # 1. 获取多 Agent 配置
    service = MultiAgentService(db)
    config = service.get_config(app_id)
    
    if not config:
        return success(
            data=None,
            msg="应用未配置多 Agent，无法测试路由"
        )
    
    # 2. 准备子 Agent 信息
    sub_agents = {}
    for sub_agent_info in config.sub_agents:
        agent_id = sub_agent_info["agent_id"]
        sub_agents[agent_id] = {
            "name": sub_agent_info.get("name", agent_id),
            "role": sub_agent_info.get("role", "")
        }
    
    # 3. 获取路由模型（如果指定）
    routing_model = None
    if request.routing_model_id:
        routing_model = db.get(ModelConfig, request.routing_model_id)
        if not routing_model:
            return success(
                data=None,
                msg=f"路由模型不存在: {request.routing_model_id}"
            )
    
    # 4. 初始化路由器
    state_manager = ConversationStateManager()
    router = LLMRouter(
        db=db,
        state_manager=state_manager,
        routing_rules=config.routing_rules or [],
        sub_agents=sub_agents,
        routing_model_config=routing_model,
        use_llm=request.use_llm and routing_model is not None
    )
    
    # 5. 设置阈值
    if request.keyword_threshold:
        router.keyword_high_confidence_threshold = request.keyword_threshold
    
    # 6. 执行路由
    try:
        routing_result = await router.route(
            message=request.message,
            conversation_id=str(request.conversation_id) if request.conversation_id else None,
            force_new=request.force_new
        )
        
        # 7. 获取 Agent 信息
        agent_id = routing_result["agent_id"]
        agent_info = sub_agents.get(agent_id, {})
        
        # 8. 构建响应
        response_data = {
            "message": request.message,
            "routing_result": {
                "agent_id": agent_id,
                "agent_name": agent_info.get("name", agent_id),
                "agent_role": agent_info.get("role", ""),
                "confidence": routing_result["confidence"],
                "strategy": routing_result["strategy"],
                "topic": routing_result["topic"],
                "topic_changed": routing_result["topic_changed"],
                "reason": routing_result["reason"],
                "routing_method": routing_result["routing_method"]
            },
            "cmulti-agent/batch-test-routingonfig_info": {
                "use_llm": request.use_llm and routing_model is not None,
                "routing_model": routing_model.name if routing_model else None,
                "keyword_threshold": router.keyword_high_confidence_threshold,
                "total_sub_agents": len(sub_agents)
            }
        }
        
        return success(
            data=response_data,
            msg="路由测试成功"
        )
        
    except Exception as e:
        logger.error(f"路由测试失败: {str(e)}")
        return success(
            data=None,
            msg=f"路由测试失败: {str(e)}"
        )


@router.post(
    "/{app_id}/multi-agent/test-master-agent",
    summary="测试 Master Agent 决策"
)
async def test_master_agent(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: multi_agent_schema.RoutingTestRequest = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试 Master Agent 的路由决策能力
    
    这个接口专门用于测试新的 Master Agent 路由器，
    可以看到 Master Agent 的完整决策过程。
    
    返回信息包括：
    - 选中的 Agent
    - 置信度
    - 决策理由
    - 是否需要协作
    - 路由策略（master_agent / rule_fast_path / fallback）
    """
    from app.services.conversation_state_manager import ConversationStateManager
    from app.services.master_agent_router import MasterAgentRouter
    from app.models import ModelConfig
    
    # 1. 获取多 Agent 配置
    service = MultiAgentService(db)
    config = service.get_config(app_id)
    
    if not config:
        return success(
            data=None,
            msg="应用未配置多 Agent，无法测试"
        )
    
    # 2. 加载 Master Agent
    from app.models import AppRelease, App
    
    master_release = db.get(AppRelease, config.master_agent_id)
    if not master_release:
        return success(
            data=None,
            msg=f"Master Agent 发布版本不存在: {config.master_agent_id}"
        )
    
    # 获取应用信息
    app = db.get(App, master_release.app_id)
    if not app:
        return success(
            data=None,
            msg=f"应用不存在: {master_release.app_id}"
        )
    
    # 创建 Master Agent 代理对象
    class AgentConfigProxy:
        def __init__(self, release, app, config_data):
            self.id = release.id
            self.app_id = release.app_id
            self.app = app
            self.name = release.name
            self.description = release.description
            self.system_prompt = config_data.get("system_prompt")
            self.default_model_config_id = release.default_model_config_id
    
    config_data = master_release.config or {}
    master_agent_config = AgentConfigProxy(master_release, app, config_data)
    
    # 3. 获取 Master Agent 的模型配置
    master_model_config = db.get(ModelConfig, master_agent_config.default_model_config_id)
    if not master_model_config:
        return success(
            data=None,
            msg=f"Master Agent 模型配置不存在: {master_agent_config.default_model_config_id}"
        )
    
    # 4. 准备子 Agent 信息
    sub_agents = {}
    for sub_agent_info in config.sub_agents:
        agent_id = sub_agent_info["agent_id"]
        
        # 加载子 Agent
        sub_release = db.get(AppRelease, uuid.UUID(agent_id))
        if sub_release:
            sub_app = db.get(App, sub_release.app_id)
            sub_config_data = sub_release.config or {}
            sub_agent_config = AgentConfigProxy(sub_release, sub_app, sub_config_data)
            
            sub_agents[agent_id] = {
                "config": sub_agent_config,
                "info": sub_agent_info
            }
    
    # 5. 初始化 Master Agent 路由器
    state_manager = ConversationStateManager()
    router = MasterAgentRouter(
        db=db,
        master_agent_config=master_agent_config,
        master_model_config=master_model_config,
        sub_agents=sub_agents,
        state_manager=state_manager,
        enable_rule_fast_path=True
    )
    
    # 6. 执行路由决策
    try:
        decision = await router.route(
            message=request.message,
            conversation_id=str(request.conversation_id) if request.conversation_id else None,
            variables=None
        )
        
        # 7. 获取选中的 Agent 信息
        agent_id = decision["selected_agent_id"]
        agent_info = sub_agents.get(agent_id, {}).get("info", {})
        
        # 8. 构建响应
        response_data = {
            "message": request.message,
            "master_agent": {
                "name": master_agent_config.name,
                "model": master_model_config.name
            },
            "decision": {
                "selected_agent_id": agent_id,
                "selected_agent_name": agent_info.get("name", "未知"),
                "selected_agent_role": agent_info.get("role", ""),
                "confidence": decision["confidence"],
                "reasoning": decision.get("reasoning", ""),
                "topic": decision.get("topic", ""),
                "strategy": decision["strategy"],
                "routing_method": decision.get("routing_method", ""),
                "need_collaboration": decision.get("need_collaboration", False),
                "collaboration_agents": decision.get("collaboration_agents", [])
            },
            "config_info": {
                "total_sub_agents": len(sub_agents),
                "enable_rule_fast_path": True
            }
        }
        
        return success(
            data=response_data,
            msg="Master Agent 决策测试成功"
        )
        
    except Exception as e:
        logger.error(f"Master Agent 决策测试失败: {str(e)}")
        return success(
            data=None,
            msg=f"测试失败: {str(e)}"
        )


@router.post(
    "/{app_id}/multi-agent/batch-test-routing",
    summary="批量测试智能路由"
)
async def batch_test_routing(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: multi_agent_schema.BatchRoutingTestRequest = ...,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """批量测试智能路由功能
    
    用于测试多条消息的路由效果，并统计准确率
    
    参数：
    - test_cases: 测试用例列表
    - routing_model_id: 路由模型 ID（可选）
    - use_llm: 是否启用 LLM
    - keyword_threshold: 关键词置信度阈值
    """
    from app.services.conversation_state_manager import ConversationStateManager
    from app.services.llm_router import LLMRouter
    from app.models import ModelConfig
    
    # 1. 获取多 Agent 配置
    service = MultiAgentService(db)
    config = service.get_config(app_id)
    
    if not config:
        return success(
            data=None,
            msg="应用未配置多 Agent，无法测试路由"
        )
    
    # 2. 准备子 Agent 信息
    sub_agents = {}
    for sub_agent_info in config.sub_agents:
        agent_id = sub_agent_info["agent_id"]
        sub_agents[agent_id] = {
            "name": sub_agent_info.get("name", agent_id),
            "role": sub_agent_info.get("role", "")
        }
    
    # 3. 获取路由模型
    routing_model = None
    if request.routing_model_id:
        routing_model = db.get(ModelConfig, request.routing_model_id)
    
    # 4. 初始化路由器
    state_manager = ConversationStateManager()
    router = LLMRouter(
        db=db,
        state_manager=state_manager,
        routing_rules=config.routing_rules or [],
        sub_agents=sub_agents,
        routing_model_config=routing_model,
        use_llm=request.use_llm and routing_model is not None
    )
    
    if request.keyword_threshold:
        router.keyword_high_confidence_threshold = request.keyword_threshold
    
    # 5. 批量测试
    results = []
    correct_count = 0
    total_count = len(request.test_cases)
    
    for test_case in request.test_cases:
        try:
            routing_result = await router.route(
                message=test_case.message,
                conversation_id=str(uuid.uuid4())  # 每个测试用例使用独立会话
            )
            
            agent_id = routing_result["agent_id"]
            agent_info = sub_agents.get(agent_id, {})
            
            # 判断是否正确
            is_correct = None
            if test_case.expected_agent_id:
                is_correct = (agent_id == str(test_case.expected_agent_id))
                if is_correct:
                    correct_count += 1
            
            results.append({
                "message": test_case.message,
                "description": test_case.description,
                "routed_agent_id": agent_id,
                "routed_agent_name": agent_info.get("name"),
                "expected_agent_id": str(test_case.expected_agent_id) if test_case.expected_agent_id else None,
                "is_correct": is_correct,
                "confidence": routing_result["confidence"],
                "routing_method": routing_result["routing_method"],
                "strategy": routing_result["strategy"]
            })
            
        except Exception as e:
            logger.error(f"测试用例失败: {test_case.message}, 错误: {str(e)}")
            results.append({
                "message": test_case.message,
                "description": test_case.description,
                "error": str(e)
            })
    
    # 6. 统计
    accuracy = None
    if correct_count > 0:
        total_with_expected = sum(1 for r in results if r.get("expected_agent_id"))
        if total_with_expected > 0:
            accuracy = correct_count / total_with_expected * 100
    
    response_data = {
        "total_count": total_count,
        "correct_count": correct_count,
        "accuracy": accuracy,
        "results": results,
        "config_info": {
            "use_llm": request.use_llm and routing_model is not None,
            "routing_model": routing_model.name if routing_model else None,
            "keyword_threshold": router.keyword_high_confidence_threshold
        }
    }
    
    return success(
        data=response_data,
        msg=f"批量测试完成，准确率: {accuracy:.1f}%" if accuracy else "批量测试完成"
    )
