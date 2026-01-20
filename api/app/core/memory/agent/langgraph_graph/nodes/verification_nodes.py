
from app.core.logging_config import get_agent_logger
from app.db import get_db

from app.core.memory.agent.models.verification_models import VerificationResult
from app.core.memory.agent.utils.llm_tools import (
    PROJECT_ROOT_,
    ReadState,
)
from app.core.memory.agent.utils.redis_tool import store
from app.core.memory.agent.utils.session_tools import SessionService
from app.core.memory.agent.utils.template_tools import TemplateService
from app.core.memory.agent.services.optimized_llm_service import LLMServiceMixin

template_root = PROJECT_ROOT_ + '/agent/utils/prompt'
db_session = next(get_db())
logger = get_agent_logger(__name__)

class VerificationNodeService(LLMServiceMixin):
    """验证节点服务类"""
    
    def __init__(self):
        super().__init__()
        self.template_service = TemplateService(template_root)

# 创建全局服务实例
verification_service = VerificationNodeService()

async def Verify_prompt(state: ReadState,messages_deal):
    storage_type = state.get('storage_type', '')
    user_rag_memory_id = state.get('user_rag_memory_id', '')
    data = state.get('data', '')
    Verify_result = {
        "status": messages_deal.split_result,
        "verified_data": messages_deal.expansion_issue,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id,
        "_intermediate": {
            "type": "verification",
            "title": "Data Verification",
            "result": messages_deal.split_result,
            "reason": messages_deal.reason,
            "query": data,
            "verified_count": len(messages_deal.expansion_issue),
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
    }
    return Verify_result
async def Verify(state: ReadState):
    content = state.get('data', '')
    group_id = state.get('group_id', '')
    memory_config = state.get('memory_config', None)

    history = await SessionService(store).get_history(group_id, group_id, group_id)

    retrieve = state.get("retrieve", '')
    retrieve = retrieve.get("Expansion_issue", [])
    messages = {
        "Query": content,
        "Expansion_issue": retrieve
    }

    system_prompt = await verification_service.template_service.render_template(
        template_name='split_verify_prompt.jinja2',
        operation_name='split_verify_prompt',
        history=history,
        sentence=messages
    )
    
    # 使用优化的LLM服务
    structured = await verification_service.call_llm_structured(
        state=state,
        db_session=db_session,
        system_prompt=system_prompt,
        response_model=VerificationResult,
        fallback_value={
            "split_result": "fail",
            "expansion_issue": [],
            "reason": "验证失败"
        }
    )
    
    result = await Verify_prompt(state, structured)
    return {"verify": result}