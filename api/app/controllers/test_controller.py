from fastapi import APIRouter, Depends, status, Query, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid


from app.core.models import RedBearLLM, RedBearRerank
from app.core.models.base import RedBearModelConfig
from app.core.models.embedding import RedBearEmbeddings
from app.db import get_db
from app.dependencies import get_current_user
from app.models.models_model import ModelApiKey, ModelProvider, ModelType
from app.models.user_model import User
from app.schemas import model_schema
from app.core.response_utils import success
from app.schemas.response_schema import ApiResponse, PageData
from app.services.model_service import ModelConfigService, ModelApiKeyService
from app.core.logging_config import get_api_logger

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/test",
    tags=["test"],
)


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
        # ChatPromptTemplate
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

    data =  [
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
    data =  [
            "最近哪家咖啡店评价最好？",
            "附近有没有推荐的咖啡厅？",
            "明天天气预报说会下雨。",
            "北京是中国的首都。",
            "我想找一个适合学习的地方。"
        ]
    scores = model.rerank(query=query, documents=data, top_n=3)
    print(scores)
    return success(msg="测试Rerank成功", data={"query": query, "documents": data, "scores": scores})
