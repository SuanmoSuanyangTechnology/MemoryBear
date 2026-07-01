"""记忆读写服务接口 - 基于 JWT 认证

将同步读、异步写从 memory_agent_controller 迁出，统一收口本文件，
使对内 /api 路由与对外 /v1（memory_api_controller）保持一致。

路由前缀: /memory
认证方式: JWT Token
"""
import html

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.enums import Neo4jNodeType, SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.response_utils import fail, success
from app.db import get_db, get_db_read
from app.dependencies import cur_workspace_access_guard, cur_workspace_access_guard_self_db, get_current_user
from app.models.user_model import User
from app.repositories import knowledge_repository
from app.schemas.memory_agent_schema import StorageType, UserInput, Write_UserInput
from app.schemas.response_schema import ApiResponse
from app.services import workspace_service
from app.services.memory_agent_service import MemoryAgentService
from app.services.memory_config_service import MemoryConfigService
from app.utils.tmp_session import ChatSessionCache

api_logger = get_api_logger()

memory_agent_service = MemoryAgentService()

router = APIRouter(
    prefix="/memory",
    tags=["Memory"],
)


@router.post("/write", response_model=ApiResponse)
@cur_workspace_access_guard()
async def write_server_async(
        user_input: Write_UserInput,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Async write service endpoint - enqueues write processing to Celery

    Args:
        user_input: Write request containing message and end_user_id
        language_type: 语言类型 ("zh" 中文, "en" 英文)，通过 X-Language-Type Header 传递

    Returns:
        Task ID for tracking async operation
    """
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    config_id = MemoryConfigService(db).get_config_id_by_end_user(user_input.end_user_id)
    workspace_id = current_user.current_workspace_id
    api_logger.info(
        f"Async write service: workspace_id={workspace_id}, config_id={config_id}, language_type={language}")

    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None: storage_type = 'neo4j'
    user_rag_memory_id = ''
    if workspace_id:

        knowledge = knowledge_repository.get_knowledge_by_name(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id
        )
        if knowledge: user_rag_memory_id = str(knowledge.id)
    api_logger.info(f"Async write: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    try:
        # ── RAG 路径：保持不变，直接拼接文本写向量库 ──
        if storage_type and storage_type.lower() == StorageType.RAG.value:
            messages_list = memory_agent_service.get_messages_list(user_input)
            await MemoryService.write_messages_to_rag(
                messages=messages_list,
                end_user_id=user_input.end_user_id,
                user_rag_memory_id=user_rag_memory_id,
            )
            api_logger.info(f"RAG write completed for end_user={user_input.end_user_id}")
            return success(data={}, msg="RAG 写入完成")

        # ── Neo4j 路径：通过 dispatcher 写入 ──
        workspace_id_str = str(current_user.current_workspace_id) if current_user.current_workspace_id else ""
        messages_list = memory_agent_service.get_messages_list(user_input)

        task_ids = await MemoryService.dispatch_api_service_async(
            messages=messages_list,
            end_user_id=user_input.end_user_id,
            config_id=config_id,
            workspace_id=workspace_id_str,
            language=language,
        )

        api_logger.info(
            f"Write tasks queued: {len(task_ids)} tasks, end_user={user_input.end_user_id}"
        )

        return success(data={"task_ids": task_ids}, msg=f"已提交 {len(task_ids)} 个写入任务")
    except Exception as e:
        api_logger.error(f"Async write operation failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "写入失败", str(e))


@router.post("/read/sync", response_model=ApiResponse)
@cur_workspace_access_guard_self_db()
async def read_server(
        user_input: UserInput,
        current_user: User = Depends(get_current_user)
):
    """
    Read service endpoint - processes read operations synchronously

    search_switch values:
    - "0": Requires verification
    - "1": No verification, direct split
    - "2": Direct answer based on context
    """
    config_id = user_input.config_id
    workspace_id = current_user.current_workspace_id

    with get_db_read() as db:
        storage_type = workspace_service.get_workspace_storage_type(
            db=db,
            workspace_id=workspace_id,
            user=current_user
        )
        if storage_type is None:
            storage_type = 'neo4j'
        user_rag_memory_id = ''
        memory_config_id = MemoryConfigService(db).get_config_id_by_end_user(user_input.end_user_id)
        if workspace_id:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db,
                name="USER_RAG_MERORY",
                workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)

    session_id = user_input.session_id.hex

    api_logger.info(
        f"Read service: group={user_input.end_user_id}, storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}, workspace_id={workspace_id}, session_id={session_id}")
    try:
        service = MemoryService(
            memory_config_id,
            end_user_id=user_input.end_user_id,
            draft=True
        )
        session_cache = ChatSessionCache(session_id)
        search_result = await service.read(
            user_input.message,
            SearchStrategy(user_input.search_switch),
            history=await session_cache.get_history(),
        )
        intermediate_outputs = []
        sub_queries = set()
        for memory in search_result.memories:
            sub_queries.add(str(memory.query))
        idx = 0
        if user_input.search_switch in [SearchStrategy.DEEP, SearchStrategy.NORMAL]:
            intermediate_outputs.append({
                "type": "problem_split",
                "title": "问题拆分",
                "data": [
                    {
                        "id": f"Q{(idx := idx + 1)}",
                        "question": question
                    }
                    for question in sub_queries
                    if question
                ]
            })
        perceptual_data = [
            memory.data
            for memory in search_result.memories
            if memory.source == Neo4jNodeType.PERCEPTUAL
        ]

        if len(perceptual_data):
            intermediate_outputs.append({
                "type": "perceptual_retrieve",
                "title": "感知记忆检索",
                "data": perceptual_data,
                "total": len(perceptual_data),
            })
        intermediate_outputs.append({
            "type": "search_result",
            "title": f"合并检索结果 (共{len(sub_queries)}个查询,{len(search_result.memories)}条结果)",
            "result": search_result.content,
            "raw_result": search_result.memories,
            "total": len(search_result.memories),
        })
        answer = await memory_agent_service.generate_summary_from_retrieve(
            end_user_id=user_input.end_user_id,
            retrieve_info=search_result.content,
            history=[],
            query=user_input.message,
            config_id=config_id,
        )
        await session_cache.append_many(
            [
                {"role": "user", "content": user_input.message},
                {"role": "assistant", "content": answer}
            ]
        )
        for _ in intermediate_outputs:
            if _["type"] == "search_result":
                _["result"] = html.escape(_["result"])
        result = {
            'answer': answer,
            "intermediate_outputs": intermediate_outputs,
            "session_id": session_id,
        }

        return success(data=result, msg="回复对话消息成功")
    except BaseException as e:
        # Handle ExceptionGroup from TaskGroup (Python 3.11+) or BaseExceptionGroup
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
            api_logger.error(f"Read operation error (TaskGroup): {detailed_error}", exc_info=True)
            return fail(BizCode.INTERNAL_ERROR, "回复对话消息失败", detailed_error)
        api_logger.error(f"Read operation error: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "回复对话消息失败", str(e))
