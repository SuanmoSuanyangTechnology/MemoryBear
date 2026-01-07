from fastapi import APIRouter, Depends, status, HTTPException, Body, Path
from fastapi.responses import StreamingResponse
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy.orm import Session
import uuid

from app.core.models import RedBearLLM, RedBearRerank
from app.core.models.base import RedBearModelConfig
from app.core.models.embedding import RedBearEmbeddings
from app.db import get_db
from app.models.models_model import ModelApiKey
from app.core.response_utils import success
from app.schemas.response_schema import ApiResponse
from app.schemas.app_schema import AppChatRequest
from app.services.model_service import ModelConfigService
from app.services.handoffs_service import get_handoffs_service_for_app, reset_handoffs_service_cache
from app.services.conversation_service import ConversationService
from app.core.logging_config import get_api_logger
from app.dependencies import get_current_user

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/test",
    tags=["test"],
)


# ==================== 原有测试接口 ====================

@router.get("/llm/{model_id}", response_model=ApiResponse)
def test_llm(
    model_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    if not config:
        api_logger.error(f"模型ID {model_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型ID不存在")
    try:
        apiConfig: ModelApiKey = config.api_keys[0]
        llm = RedBearLLM(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,            
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ), type=config.type)
        print(llm.dict())

        template = """Question: {question}

Answer: Let's think step by step."""
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | llm
        answer = chain.invoke({"question": "What is LangChain?"})
        print("Answer:", answer)
        return success(msg="测试LLM成功", data={"question": "What is LangChain?", "answer": answer})
       
    except Exception as e:
        api_logger.error(f"测试LLM失败: {str(e)}")
        raise


@router.get("/embedding/{model_id}", response_model=ApiResponse)
def test_embedding(
    model_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    if not config:
        api_logger.error(f"模型ID {model_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型ID不存在")

    apiConfig: ModelApiKey = config.api_keys[0]
    model = RedBearEmbeddings(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ))

    data = [
        "最近哪家咖啡店评价最好？",
        "附近有没有推荐的咖啡厅？",
        "明天天气预报说会下雨。",
        "北京是中国的首都。",
        "我想找一个适合学习的地方。"
    ]
    embeddings = model.embed_documents(data)
    print(embeddings)
    query = "我想找一个适合学习的地方。"
    query_embedding = model.embed_query(query)
    print(query_embedding)

    return success(msg="测试LLM成功")
       

@router.get("/rerank/{model_id}", response_model=ApiResponse)
def test_rerank(
    model_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    if not config:
        api_logger.error(f"模型ID {model_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型ID不存在")

    apiConfig: ModelApiKey = config.api_keys[0]
    model = RedBearRerank(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ))
    query = "最近哪家咖啡店评价最好？"
    data = [
        "最近哪家咖啡店评价最好？",
        "附近有没有推荐的咖啡厅？",
        "明天天气预报说会下雨。",
        "北京是中国的首都。",
        "我想找一个适合学习的地方。"
    ]
    scores = model.rerank(query=query, documents=data, top_n=3)
    print(scores)
    return success(msg="测试Rerank成功", data={"query": query, "documents": data, "scores": scores})


# ==================== Handoffs 测试接口 ====================

@router.post("/handoffs/{app_id}")
async def test_handoffs(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    request: AppChatRequest = Body(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """测试 Agent Handoffs 功能
    
    演示 LangGraph 实现的多 Agent 协作和动态切换
    
    - 从数据库 multi_agent_config 获取 Agent 配置
    - 根据用户问题自动切换到合适的 Agent
    - 使用 conversation_id 保持会话状态
    - 通过 stream 参数控制是否流式输出
    
    事件类型（流式）：
    - start: 开始执行
    - agent: 当前 Agent 信息
    - message: 流式消息内容
    - handoff: Agent 切换事件
    - end: 执行结束
    - error: 错误信息
    """
    try:
        workspace_id = current_user.current_workspace_id
        
        # 获取或创建会话
        conversation_service = ConversationService(db)
        
        if request.conversation_id:
            # 验证会话存在
            conversation = conversation_service.get_conversation(uuid.UUID(request.conversation_id))
            if not conversation:
                raise HTTPException(status_code=404, detail="会话不存在")
            conversation_id = str(conversation.id)
        else:
            # 创建新会话
            conversation = conversation_service.create_or_get_conversation(
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=request.user_id,
                is_draft=True
            )
            conversation_id = str(conversation.id)
        
        # 根据 stream 参数决定返回方式
        if request.stream:
            # 流式返回
            service = get_handoffs_service_for_app(app_id, db, streaming=True)
            return StreamingResponse(
                service.chat_stream(
                    message=request.message,
                    conversation_id=conversation_id
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        else:
            # 非流式返回
            service = get_handoffs_service_for_app(app_id, db, streaming=False)
            result = await service.chat(
                message=request.message,
                conversation_id=conversation_id
            )
            return success(data=result, msg="Handoffs 测试成功")
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Handoffs 测试失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/handoffs/{app_id}/agents", response_model=ApiResponse)
def get_handoff_agents(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """获取应用的 Handoff Agent 列表"""
    try:
        service = get_handoffs_service_for_app(app_id, db, streaming=False)
        agents = service.get_agents()
        return success(data={"agents": agents}, msg="获取 Agent 列表成功")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        api_logger.error(f"获取 Agent 列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/handoffs/{app_id}/reset")
def reset_handoff_service(
    app_id: uuid.UUID = Path(..., description="应用 ID"),
    current_user=Depends(get_current_user)
):
    """重置指定应用的 Handoff 服务缓存"""
    reset_handoffs_service_cache(app_id)
    return success(msg="Handoff 服务已重置")
