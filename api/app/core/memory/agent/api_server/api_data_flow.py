# from app.core.memory.agent.mcp_server.mcp_data_flow_server import status_typle
# import json
# import os
# import re
# import asyncio
# import time
# from datetime import datetime
# from threading import Lock
# from typing import Dict

# import requests
# from dotenv import load_dotenv
# from fastapi.responses import FileResponse
# from fastapi import FastAPI, APIRouter
# from langchain_mcp_adapters.client import MultiServerMCPClient
# from langchain_mcp_adapters.tools import load_mcp_tools
# from langgraph.checkpoint.memory import InMemorySaver
# from pydantic import BaseModel

# from fastapi.middleware.cors import CORSMiddleware

# from app.core.memory.agent.langgraph_graph.read_graph import make_read_graph
# from app.core.memory.agent.langgraph_graph.write_graph import make_write_graph
# from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
# from app.core.memory.agent.utils.mcp_tools import get_mcp_server_config
# from celery.result import AsyncResult
# from app.core.memory.agent.tasks.celery_app import celery_app
# from app.core.memory.agent.tasks.message_tasks import read_message_task, write_message_task
# from app.core.memory.agent.utils.distributed_lock import get_redis_client

# '''
# search_switch:0 (需要走验证)
# search_switch：1 （不需要走验证，直接走拆分）
# search_switch：2 （直接根据上下文回答）
# '''
# user_locks = {}
# locks_lock = Lock()
# app = FastAPI()
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"]
# )
# router = APIRouter(
#     prefix="/memory",
#     tags=["Memory"],
# )

# class UserInputTest(BaseModel):
#     user_id: str
#     message: str
#     search_switch:str
#     history: list[dict]

# class Write_UserInput(BaseModel):
#     user_id: str
#     message: str
#     apply_id:str
#     group_id:str


# def get_user_lock(user_id: str) -> Lock:
#     """获取特定用户的锁"""
#     with locks_lock:
#         if user_id not in user_locks:
#             user_locks[user_id] = Lock()
#         return user_locks[user_id]
# def extract_tool_call_info(event):
#     """提取工具调用信息"""
#     last_message = event["messages"][-1]

#     # 检查是否为 AI 消息且包含工具调用
#     if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
#         tool_calls = last_message.tool_calls
#         for i, tool_call in enumerate(tool_calls):
#             if isinstance(tool_call, dict):
#                 tool_call_id = tool_call.get('id')
#                 tool_name = tool_call.get('name')
#                 tool_args = tool_call.get('args', {})
#             else:
#                 tool_call_id = getattr(tool_call, 'id', None)
#                 tool_name = getattr(tool_call, 'name', None)
#                 tool_args = getattr(tool_call, 'args', {})

#             # 输出工具调用信息
#             print (f"Tool Call {i + 1}: ID={tool_call_id}, Name={tool_name}, Args={tool_args}")
#         return True

#     # 检查是否为工具消息
#     elif hasattr(last_message, 'tool_call_id'):
#         tool_call_id = getattr(last_message, 'tool_call_id', None)
#         # 如果是 Summary_fails 工具调用，提取相关信息
#         if hasattr(last_message, 'name') and hasattr(last_message, 'content'):
#             tool_name = getattr(last_message, 'name', None)
#             try:
#                 content = json.loads(getattr(last_message, 'content', '{}'))
#                 tool_args = content.get('args', {})
#                 print (f"Tool Call 1: ID={tool_call_id}, Name={tool_name}, Args={tool_args}")
#             except:
#                 print (f"Tool Response ID: {tool_call_id}")
#         else:
#             print(f"Tool Response ID: {tool_call_id}")
#         return True

#     return False

# @router.get("/health/status")
# async def get_health_status() -> Dict:
#     """获取由 Celery 周期任务写入的最新健康状态"""
#     try:
#         client = get_redis_client()
#         payload = client.hgetall("memsci:health:read_service") or {}
#         if payload:
#             # decode bytes to str
#             decoded = {k.decode("utf-8"): v.decode("utf-8") for k, v in payload.items()}
#             status = decoded.get("status", "unknown")
#             msg = decoded.get("msg", "OK" if status == "Success" else "接口请求失败，请检查接口状态")
#             code = int(decoded.get("code", "0" if status == "Success" else "500"))
#             error = decoded.get("error", "" if status == "Success" else "Error")
#             ts = int(float(decoded.get("time", str(time.time()))))
#         else:
#             status = "unknown"
#             msg = "Not yet checked"
#             code = 0
#             error = ""
#             ts = int(time.time())
#         return {
#             "code": code,
#             "msg": msg,
#             "data": status,
#             "error": error,
#             "time": ts,
#         }
#     except Exception as e:
#         return {
#             "code": 500,
#             "msg": "健康状态查询失败",
#             "data": "",
#             "error": str(e),
#             "time": int(time.time()),
#         }

# @router.get("/download_log")
# def download_log():
#     log_dir = os.path.join(PROJECT_ROOT_, "agent", "logger_file", 'logs')
#     log_path =os.path.join(log_dir, f"agent_service.log")
#     result=FileResponse(
#         path=log_path,
#         filename="app.log",
#         media_type="text/plain"
#     )
#     pattern = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - \[INFO\] - __main__ - ')

#     summer=''

#     with open(log_path, "r", encoding="utf-8") as infile:
#         for line in infile:
#             # 去除匹配到的时间戳与日志头
#             cleaned = re.sub(pattern, '', line)
#             summer+=cleaned
#     if len(summer)>=10:code=0;msg="OK"
#     else:code=500;msg="NO LOGS"
#     send_logs={
#           "code": code,
#           "msg": msg,
#           "data": summer,
#           "error": "",
#           "time": int(time.time())
#         }

#     return send_logs

# @router.post("/writer_service")
# async def write_server(user_input: Write_UserInput):
#     code=0
#     print("user_input:", user_input)
#     user_id = user_input.user_id
#     user_message = user_input.message
#     apply_id=user_input.apply_id
#     group_id=user_input.group_id

#     print(f"用户Id:{user_id},本轮输入:{user_message}")
#     mcp_config = get_mcp_server_config()
#     client = MultiServerMCPClient(mcp_config)
#     async with client.session("data_flow") as session:
#         print("已连接 MCP Server：data_flow")
#         tools = await load_mcp_tools(session)
#         async with make_write_graph(user_id,tools,apply_id,group_id) as graph:
#             print("创建图成功")

#             # 添加配置参数，包含必需的 thread_id
#             config = {"configurable": {"thread_id": user_id}}  # 使用 user_id 作为线程ID

#             async for event in graph.astream(
#                     {"messages": user_message},
#                     stream_mode="values",
#                     config=config  # 添加配置参数
#             ):
#                 messages = event.get('messages')
#     messages = str(messages).replace("'", '"').replace('\\n', '').replace('\n', '').replace('\\', '')
#     countext = re.findall(r'"status": "(.*?)",', messages)[0]
#     if countext=='success':
#         result={
#         "code": code,
#         "msg": "写入成功",
#         "data": countext,
#         "error": "",
#         "time": int(time.time())
#     }
#     else:
#         result = {
#             "code": 500,
#             "msg": "写入失败",
#             "data": countext,
#             "error": messages,
#             "time": int(time.time())
#         }
#     return  result

# @router.post("/writer_service_async")
# async def write_server_async(user_input: Write_UserInput):
#     """Enqueue write processing to Celery and return a task id."""
#     try:
#         task = write_message_task.delay(user_input.user_id, user_input.message)
#         return {
#             "code": 0,
#             "msg": "写入任务已排队",
#             "data": {"task_id": task.id},
#             "error": "",
#             "time": int(time.time())
#         }
#     except Exception as e:
#         return {
#             "code": 500,
#             "msg": "写入任务排队失败",
#             "data": "",
#             "error": str(e),
#             "time": int(time.time())
#         }

# @router.post("/read_service")
# async def read_server(user_input: UserInput):
#     code=0
#     print("user_input:", user_input)
#     user_id = user_input.user_id
#     user_message = user_input.message
#     search_switch=user_input.search_switch
#     apply_id=user_input.apply_id
#     group_id=user_input.group_id

#     # 为当前用户获取锁
#     user_lock = get_user_lock(user_id)

#     # 使用用户锁保护整个处理过程
#     with user_lock:
#         history = user_input.history
#         history.append({"role": "user", "content": user_message})
#         print(f"用户Id:{user_id},本轮输入:{user_message},历史记录:{history}")

#         mcp_config = get_mcp_server_config()
#         client = MultiServerMCPClient(mcp_config)
#         # 创建内存检查点保存器（短期记忆）
#         memory = InMemorySaver()
#         async with client.session('data_flow') as session:
#             print("✅ 已连接 MCP Server：data_flow")
#             tools = await load_mcp_tools(session)

#             async with make_read_graph(user_id,tools,search_switch,apply_id,group_id) as graph:
#                 start=time.time()
#                 # 添加配置参数，包含必需的 thread_id
#                 config = {"configurable": {"thread_id": user_id}}  # 使用 user_id 作为线程ID

#                 try:

#                     async for event in graph.astream(
#                             {"messages": history},
#                             stream_mode="values",
#                             config=config  # 添加配置参数
#                     ):
#                         messages = event.get('messages')

#                         if not extract_tool_call_info(event):
#                             last_message = event["messages"][-1]

#                             # 检查是否为 Summary 节点的特定消息
#                             summary_found = False

#                             # 检查工具调用ID
#                             if hasattr(last_message, 'tool_call_id') and last_message.tool_call_id:
#                                 if 'Summary_id' in last_message.tool_call_id:
#                                     summary_found = True

#                             # 检查消息内容
#                             if hasattr(last_message, 'content') and last_message.content:
#                                 if 'Summary_id' in str(last_message.content):
#                                     summary_found = True

#                             # 检查名称属性
#                             if hasattr(last_message, 'name') and last_message.name:
#                                 if 'Summary' in last_message.name:
#                                     summary_found = True

#                             if summary_found:
#                                 print("=== Summary 节点响应 ===")
#                                 messages = str(last_message).replace("'", '"').replace('\\n', '').replace('\n', '').replace('\\', '')
#                                 print('===>>',messages)
#                                 if 'Retrieve_Summary' in messages or 'name="Summary"' in messages :
#                                     messages = re.findall(r'"summary_result": "(.*?)"', str(messages))
#                                     messages=messages[-1]
#                                 # if 'name="Summary"' in messages:
#                                 #     messages=re.findall(f'"query_answer": "(.*?)"',messages)[-1]
#                                 if 'Input_Summary' in messages:
#                                     messages = re.findall(f'query_answer": "(.*?)"', messages)[-1]
#                                 end=time.time()
#                                 try:
#                                     duration = end - start
#                                 except Exception:
#                                     duration = 0.0
#                                 print(f"[make_read_graph] 创建工作流耗时: {duration}")
#                                 resulr= {
#                                     "code": code,
#                                     "msg": '回复对话消息成功',
#                                     "data": messages,
#                                     "error": "",
#                                     "time": int(time.time())
#                                 }
#                                 return resulr
#                                 # return {"history": history, "message": messages}
#                             else:
#                                 print("=== 其他消息 ===")
#                                 end = time.time()
#                                 try:
#                                     duration = end - start
#                                 except Exception:
#                                     duration = 0.0
#                                 print(f"[make_read_graph] 创建工作流耗时: {duration}")
#                                 # 其他消息正常打印
#                                 last_message.pretty_print()
#                 except Exception as e:
#                     print(f"[make_read_graph] 创建工作流异常: {e}")
#                     result = {
#                         "code": 500,
#                         "msg": '回复对话消息失败',
#                         "data": '',
#                         "error": e,
#                         "time": int(time.time())
#                     }
#                     return result

#         try:
#             messages = re.findall(r'"query_answer": "(.*?)"', str(messages))
#             messages = messages[-1]
#             end = time.time()
#             try:
#                 duration = end - start
#             except Exception:
#                 duration = 0.0
#             print(f"[make_read_graph] 创建工作流耗时: {duration}")
#             result = {
#                 "code": code,
#                 "msg": '回复对话消息成功',
#                 "data": messages,
#                 "error": '',
#                 "time": int(time.time())
#             }
#             return result
#         except Exception as e:
#             result= {
#                         "code": 500,
#                         "msg": '回复对话消息失败',
#                         "data": '',
#                         "error": e,
#                         "time": int(time.time())
#                     }
#             return result

# @router.post("/read_service_async")
# async def read_server_async(user_input: UserInput):
#     """Enqueue read processing to Celery and return a task id."""
#     try:
#         # History comes in as list[dict]; pass through to task
#         task = read_message_task.delay(user_input.user_id, user_input.message, user_input.history, user_input.search_switch)
#         return {
#             "code": 0,
#             "msg": "读取任务已排队",
#             "data": {"task_id": task.id},
#             "error": "",
#             "time": int(time.time())
#         }
#     except Exception as e:
#         return {
#             "code": 500,
#             "msg": '读取任务排队失败',
#             "data": '',
#             "error": e,
#             "time": int(time.time())
#         }


# @app.post("/read_test_service")
# async def read_server(user_input: UserInputTest):
#     code=0
#     print("user_input:", user_input)
#     user_id = user_input.user_id
#     user_message = user_input.message
#     search_switch=user_input.search_switch
#     # 为当前用户获取锁
#     user_lock = get_user_lock(user_id)

#     # 使用用户锁保护整个处理过程
#     with user_lock:
#         history = user_input.history
#         history.append({"role": "user", "content": user_message})
#         print(f"用户Id:{user_id},本轮输入:{user_message},历史记录:{history}")

#         mcp_config = get_mcp_server_config()
#         client = MultiServerMCPClient(mcp_config)
#         # 创建内存检查点保存器（短期记忆）
#         memory = InMemorySaver()
#         async with client.session('data_flow') as session:
#             print("✅ 已连接 MCP Server：data_flow")
#             tools = await load_mcp_tools(session)

#             async with make_read_graph(user_id,tools,search_switch,'test','test') as graph:
#                 start=time.time()
#                 # 添加配置参数，包含必需的 thread_id
#                 config = {"configurable": {"thread_id": user_id}}  # 使用 user_id 作为线程ID

#                 try:
#                     print(f"*/-/*-/-/-/-*/*-/-*/-/-/-/--*")
#                     async for event in graph.astream(
#                             {"messages": history},
#                             stream_mode="values",
#                             config=config  # 添加配置参数
#                     ):
#                         messages = event.get('messages')
#                         print(f"wo zai read_server =======")
#                         if not extract_tool_call_info(event):
#                             last_message = event["messages"][-1]

#                             # 检查是否为 Summary 节点的特定消息
#                             summary_found = False

#                             # 检查工具调用ID
#                             if hasattr(last_message, 'tool_call_id') and last_message.tool_call_id:
#                                 if 'Summary_id' in last_message.tool_call_id:
#                                     summary_found = True

#                             # 检查消息内容
#                             if hasattr(last_message, 'content') and last_message.content:
#                                 if 'Summary_id' in str(last_message.content):
#                                     summary_found = True

#                             # 检查名称属性
#                             if hasattr(last_message, 'name') and last_message.name:
#                                 if 'Summary' in last_message.name:
#                                     summary_found = True

#                             if summary_found:
#                                 print("=== Summary 节点响应 ===")
#                                 messages = str(last_message).replace("'", '"').replace('\\n', '').replace('\n', '').replace('\\', '')
#                                 print('===>>',messages)
#                                 if 'Retrieve_Summary' in messages or 'name="Summary"' in messages :
#                                     messages = re.findall(r'"summary_result": "(.*?)"', str(messages))
#                                     messages=messages[-1]
#                                 # if 'name="Summary"' in messages:
#                                 #     messages=re.findall(f'"query_answer": "(.*?)"',messages)[-1]
#                                 if 'Input_Summary' in messages:
#                                     messages = re.findall(f'query_answer": "(.*?)"', messages)[-1]
#                                 end=time.time()
#                                 try:
#                                     duration = end - start
#                                 except Exception:
#                                     duration = 0.0
#                                 print(f"[make_read_graph] 创建工作流耗时: {duration}")
#                                 resulr= {
#                                     "code": code,
#                                     "msg": '回复对话消息成功',
#                                     "data": messages,
#                                     "error": "",
#                                     "time": int(time.time())
#                                 }
#                                 return resulr
#                                 # return {"history": history, "message": messages}
#                             else:
#                                 print("=== 其他消息 ===")
#                                 end = time.time()
#                                 try:
#                                     duration = end - start
#                                 except Exception:
#                                     duration = 0.0
#                                 print(f"[make_read_graph] 创建工作流耗时: {duration}")
#                                 # 其他消息正常打印
#                                 last_message.pretty_print()
#                 except Exception as e:
#                     print(f"[make_read_graph] 创建工作流异常: {e}")
#                     result = {
#                         "code": 500,
#                         "msg": '回复对话消息失败',
#                         "data": '',
#                         "error": e,
#                         "time": int(time.time())
#                     }
#                     return result

#         try:
#             messages = re.findall(r'"query_answer": "(.*?)"', str(messages))
#             messages = messages[-1]
#             end = time.time()
#             try:
#                 duration = end - start
#             except Exception:
#                 duration = 0.0
#             print(f"[make_read_graph] 创建工作流耗时: {duration}")
#             result = {
#                 "code": code,
#                 "msg": '回复对话消息成功',
#                 "data": messages,
#                 "error": '',
#                 "time": int(time.time())
#             }
#             return result
#         except Exception as e:
#             result= {
#                         "code": 500,
#                         "msg": '回复对话消息失败',
#                         "data": '',
#                         "error": e,
#                         "time": int(time.time())
#                     }
#             return result

# @app.post("/status_type")
# async def status_type(user_input: Write_UserInput):
#     user_id = user_input.user_id
#     user_message = user_input.message
#     status = await status_typle(user_message)
#     print('======',status)
#     return status
# # @router.post("/read_service_async")
# # async def read_server_async(user_input: UserInput):
# #     """Enqueue read processing to Celery and return a task id."""
# #     try:
# #         # History comes in as list[dict]; pass through to task
# #         task = read_message_task.delay(user_input.user_id, user_input.message, user_input.history, user_input.search_switch)
# #         return {
# #             "code": 0,
# #             "msg": "读取任务已排队",
# #             "data": {"task_id": task.id},
# #             "error": "",
# #             "time": int(time.time())
# #         }
# #     except Exception as e:
# #         return {
# #             "code": 500,
# #             "msg": "读取任务排队失败",
# #             "data": "",
# #             "error": str(e),
# #             "time": int(time.time())
# #         }


# # @router.get("/task_status/{task_id}")
# # async def task_status(task_id: str):
# #     """Check Celery task status and return result if available."""
# #     try:
# #         res = AsyncResult(task_id, app=celery_app)
# #         state = res.state
# #         if res.ready():
# #             try:
# #                 payload = res.get(timeout=1)
# #             except Exception as e:
# #                 return {
# #                     "code": 500,
# #                     "msg": "任务结果获取失败",
# #                     "data": "",
# #                     "error": str(e),
# #                     "time": int(time.time()),
# #                     "state": state,
# #                 }
# #             return {
# #                 "code": payload.get("code", 0),
# #                 "msg": payload.get("msg", "任务完成"),
# #                 "data": payload.get("data", ""),
# #                 "error": payload.get("error", ""),
# #                 "time": payload.get("time", int(time.time())),
# #                 "state": state,
# #                 "latency": payload.get("latency"),
# #             }
# #         else:
# #             return {
# #                 "code": 0,
# #                 "msg": "任务正在执行",
# #                 "data": {"task_id": task_id},
# #                 "error": "",
# #                 "time": int(time.time()),
# #                 "state": state,
# #             }
# #     except Exception as e:
# #         return {
# #             "code": 500,
# #             "msg": "任务查询失败",
# #             "data": "",
# #             "error": str(e),
# #             "time": int(time.time()),
# #         }

# # Register router with the FastAPI app (after all routes are defined)
# app.include_router(router)


# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run(app, host="127.0.0.1", port=9210)
