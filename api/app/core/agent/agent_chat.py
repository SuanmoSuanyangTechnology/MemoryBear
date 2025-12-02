import asyncio
import os
import time

from typing import Dict, Any, List

from app.core.logging_config import get_business_logger
from app.schemas.prompt_schema import  render_prompt_message, PromptMessageRole
from app.services.api_resquests_server import messages_type, write_messages
from app.services.agent_server import ChatRequest, tool_memory, create_dynamic_agent, tool_Retrieval

logger = get_business_logger()
class Agent_chat:
    def __init__(self,config_data: dict):
        self.prompt_message = render_prompt_message(
            config_data.template_str,
            PromptMessageRole.USER,
            config_data.params
        )
        self.prompt = self.prompt_message.get_text_content()
        self.model_configs = config_data.model_configs
        self.history_memory = config_data.history_memory
        self.knowledge_base = config_data.knowledge_base
        logger.info(f"渲染结果：{self.prompt_message.get_text_content()}" )

    async def run_agent(self,agent, end_user_id:str, user_prompt:str, model_name:str):
        response = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            },
            {"configurable": {"thread_id": f'{model_name}_{end_user_id}'}},
        )
        outputs = []
        for msg in response["messages"]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                outputs.append({
                    "role": "assistant",
                    "tool_calls": [
                        {"name": t["name"], "arguments": t["args"]}
                        for t in msg.tool_calls
                    ]
                })
            elif hasattr(msg, "content") and msg.content:
                outputs.append({
                    "role": msg.__class__.__name__.lower().replace("message", ""),
                    "content": msg.content
                })
        ai_messages=[msg['content'] for msg in outputs if msg["role"] == "ai"]
        return {"model_name": model_name, "end_user_id": end_user_id, "response": ai_messages}

    async def chat(self,req: ChatRequest) -> Dict[str, Any]:

        end_user_id = req.end_user_id  # 用 user_id 作为对话线程标识
        start=time.time()
        user_prompt = req.message

        '''判断是都写入redis数据库'''
        messags_type = await messages_type(req.message,end_user_id)
        messags_type=messags_type['data']
        if messags_type=='question':
            writer_result=await write_messages(f'{end_user_id}', req.message)
            logger.info(f'判断类型写入耗时：{time.time() - start},{writer_result}')



        '''history_memory'''

        if self.history_memory==True:
            tool_result =await tool_memory(req)
            if tool_result!='' :tool_result=tool_result['data']
            if tool_result!='' :self.prompt=self.prompt+f''',历史消息：{tool_result},结合历史消息'''
            logger.info(f"记忆科学消耗时间：{time.time()-start},工具调用结果:{tool_result}")

        '''baidu'''


        '''knowledge_base'''
        if self.knowledge_base == True:
            retrieval_result=await tool_Retrieval(req)
            retrieval_knowledge = [i['page_content'] for i in retrieval_result['data']]
            retrieval_knowledge=','.join(retrieval_knowledge)
            logger.info(f"检索消耗时间：{time.time()-start},{retrieval_knowledge}")
            if retrieval_knowledge!='' :self.prompt=self.prompt+f",知识库检索内容：{retrieval_knowledge},结合检索结果"
        self.prompt=self.prompt+f'给出最合适的答案，确保答案的完整性，只保留用户的问题的回答，不额外输出提示语'
        logger.info(f"用户输入：{user_prompt}")
        logger.info(f"系统prompt：{self.prompt}")

        AGENTS = {
            cfg["name"]: await create_dynamic_agent(cfg["name"], cfg["moder_id"], self.prompt, req.token)
            for cfg in self.model_configs
        }
        tasks=[
            self.run_agent(agent, end_user_id, user_prompt, model_name)
            for model_name, agent in AGENTS.items()
        ]
        # 并行运行
        results = await asyncio.gather(*tasks)

        result=[]

        for i in results:
            result.append(i)
        chat_result=(f"最终耗时：{time.time()-start},{result}")
        return chat_result