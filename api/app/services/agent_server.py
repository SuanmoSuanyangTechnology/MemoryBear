

from typing import Any, List

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import before_model
from langchain.tools import tool
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from app.services.api_resquests_server import send_message, model, retrieval


class config(BaseModel):
    template_str:str
    params:dict
    model_configs: List[dict] = []
    history_memory:bool
    knowledge_base:bool

class RemoryInput(BaseModel):
    question: str
    end_user_id: str
    search_switch:str

class ChatRequest(BaseModel):
    end_user_id: str
    message: str
    search_switch:str
    kb_ids: List[str] = []
    similarity_threshold:float
    vector_similarity_weight:float
    top_k:int
    hybrid:bool
    token:str

class RetrievalInput(BaseModel):
    message: str
    kb_ids: List[str] = []
    similarity_threshold: float
    vector_similarity_weight: float
    top_k: int
    hybrid: bool
    token: str

async  def tool_Retrieval(req):
    tool_result = retrieval_search.invoke({
        "message":req.message, "kb_ids":req.kb_ids,
        "similarity_threshold":req.similarity_threshold, "vector_similarity_weight":req.vector_similarity_weight,
        "top_k":req.top_k, "hybrid":req.hybrid, "token":req.token
    })
    return tool_result
async def tool_memory(req):
    tool_result = remory_sk.invoke({
        "question": req.message,
        "end_user_id": req.end_user_id,
        "search_switch": req.search_switch
    })
    return tool_result


@before_model
# ========== 消息剪枝中间件 ==========
def trim_messages(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    """保留前1条 + 最近3~4条消息"""
    messages = state["messages"]
    if len(messages) <= 10:
        return None
    first_msg = messages[0]
    recent_messages = messages[-10:] if len(messages) % 2 == 0 else messages[-11:]
    new_messages = [first_msg] + recent_messages

    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *new_messages
        ]
    }

##-----------历史记忆------------
@ tool(args_schema=RemoryInput)
def remory_sk(question: str, end_user_id: str, search_switch: str):
    """
      条件调用工具：
      - 仅当 question 是疑问句时调用 send_message
      - 根据 end_user_id 和 search_switch 参数决定是否执行检索

      Args:
          question: 用户的提问内容
          end_user_id: 用户唯一标识符
          search_switch: 搜索开关，控制是否执行检索

      Returns:
          检索结果或空字符串
      """
    # 移除关于 configurable 的描述，避免混淆
    if not end_user_id or end_user_id == '123':
        print("警告: 无效的 user_id 参数")
        return ''

    if search_switch in ['on', 'off'] or not search_switch:
        print("警告: 无效的 search_switch 参数")
        return ''
    return send_message(end_user_id, question, '[]', search_switch)

#-------------检索------------


@ tool(args_schema=RetrievalInput)
def retrieval_search(message,kb_ids,similarity_threshold,vector_similarity_weight,top_k,hybrid,token):
    '''检索'''
    search=retrieval(message,kb_ids,similarity_threshold,vector_similarity_weight,top_k,hybrid,token)
    return search
async  def create_dynamic_agent(model_name: str,model_id:str,PROMPT:str,token:str):
    """根据模型名动态创建代理"""
    model_name, api_key, api_base=await model(model_id,token)
    llm = ChatOpenAI(model=model_name, base_url=api_base, temperature=0.2,api_key=api_key)
    memory = InMemorySaver()
    return create_agent(
        llm,
        tools=[remory_sk,retrieval_search],
        middleware=[trim_messages],
        checkpointer=memory,
        system_prompt=PROMPT
    )