import asyncio
import json
from collections import defaultdict
from typing import TypedDict, Annotated
import os
import logging

from jinja2 import Template
from langchain_core.messages import AnyMessage
from dotenv import load_dotenv
from langgraph.graph import add_messages
from openai import OpenAI

from app.core.memory.agent.utils.messages_tool import read_template_file
from app.core.memory.utils.config.config_utils import get_picture_config, get_voice_config
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.utils.config.definitions import SELECTED_LLM_ID, SELECTED_LLM_PICTURE_NAME, SELECTED_LLM_VOICE_NAME
from app.core.models.base import RedBearModelConfig
from app.core.memory.src.llm_tools.openai_client import OpenAIClient

PROJECT_ROOT_ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logger = logging.getLogger(__name__)

load_dotenv()

#TODO: Refactor entire picture/voice
# async  def LLM_model_request(context,data,query):
#     '''
#     Agent model request
#     Args:
#         context:Input request
#         data: template parameters
#         query:request content
#     Returns:

#     '''
#     template = Template(context)
#     system_prompt = template.render(**data)
#     llm_client = get_llm_client(SELECTED_LLM_ID)
#     result = await llm_client.chat(
#         messages=[{"role": "system", "content": system_prompt}] + [{"role": "user", "content": query}]
#     )
#     return result

async def picture_model_requests(image_url):
    '''

    Args:
        image_url:
    Returns:

    '''
    file_path = PROJECT_ROOT_ + '/agent/utils/prompt/Template_for_image_recognition_prompt.jinja2 '
    system_prompt = await read_template_file(file_path)
    result = await Picture_recognize(image_url,system_prompt)
    return (result)
class WriteState(TypedDict):
    '''
    Langgrapg Writing TypedDict
    '''
    messages: Annotated[list[AnyMessage], add_messages]
    user_id:str
    apply_id:str
    group_id:str

class ReadState(TypedDict):
    '''
       Langgrapg READING TypedDict
       name:
       id:user id
       loop_count:Traverse times
       search_switch：type
       config_id: configuration id for filtering results
       '''
    messages: Annotated[list[AnyMessage], add_messages] #消息追加的模式增加消息
    name: str
    id: str
    loop_count:int
    search_switch: str
    user_id: str
    apply_id: str
    group_id: str
    config_id: str


class COUNTState:
    '''
    The number of times the workflow dialogue retrieval content has no correct message recall traversal
    '''
    def __init__(self, limit: int = 5):
        self.total: int = 0  # 当前累加值
        self.limit: int = limit  # 最大上限

    def add(self, value: int = 1):
        """累加数字，如果达到上限就保持最大值"""
        self.total += value
        print(f"[COUNTState] 当前值: {self.total}")
        if self.total >= self.limit:
            print(f"[COUNTState] 达到上限 {self.limit}")
            self.total = self.limit  # 达到上限不再增加

    def get_total(self) -> int:
        """获取当前累加值"""
        return self.total

    def reset(self):
        """手动重置累加值"""
        self.total = 0
        print(f"[COUNTState] 已重置为 0")



# def embed(texts: list[str]) -> list[list[float]]:
#     # 这里可以换成 LangChain Embeddings
#     return [[float(len(t) % 5), float(len(t) % 3)] for t in texts]


# def export_store_to_json(store, namespace):
#     """Export the entire storage content to a JSON file"""
#     # 搜索所有存储项
#     all_items = store.search(namespace)

#     # 整理数据
#     export_data = {}
#     for item in all_items:
#         if hasattr(item, 'key') and hasattr(item, 'value'):
#             export_data[item.key] = item.value

#     # 保存到文件
#     os.makedirs("memory_logs", exist_ok=True)
#     with open("memory_logs/full_memory_export.json", "w", encoding="utf-8") as f:
#         json.dump(export_data, f, ensure_ascii=False, indent=2)

#     print(f"{len(export_data)} 条记忆到 JSON 文件")

def merge_to_key_value_pairs(data, query_key, result_key):
    grouped = defaultdict(list)
    for item in data:
        grouped[item[query_key]].append(item[result_key])
    return [{key: values} for key, values in grouped.items()]

def deduplicate_entries(entries):
    seen = set()
    deduped = []
    for entry in entries:
        key = (entry['Query_small'], entry['Result_small'])
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped



async def Picture_recognize(image_path,PROMPT_TICKET_EXTRACTION) -> str:
    try:
        model_config = get_picture_config(SELECTED_LLM_PICTURE_NAME)
    except Exception as e:
            err = f"LLM配置不可用：{str(e)}。请检查 config.json 和 runtime.json。"
            logger.error(err)
            return err
    api_key = os.getenv(model_config["api_key"])  # 从环境变量读取对应后端的 API key
    backend_model_name = model_config["llm_name"].split("/")[-1]
    api_base=model_config['api_base']

    logger.info(f"model_name: {backend_model_name}")
    logger.info(f"api_key set: {'yes' if api_key else 'no'}")
    logger.info(f"base_url: {model_config['api_base']}")

    client = OpenAI(
        api_key=api_key, base_url=api_base,
    )
    completion = client.chat.completions.create(
        model=backend_model_name,
        messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url":image_path,
                        },
                        {"type": "text",
                         "text": PROMPT_TICKET_EXTRACTION}
                    ]
                }
            ])
    picture_text = completion.choices[0].message.content
    picture_text = picture_text.replace('```json', '').replace('```', '')
    picture_text = json.loads(picture_text)
    return (picture_text['statement'])

async def  Voice_recognize():
    try:
        model_config = get_voice_config(SELECTED_LLM_VOICE_NAME)
    except Exception as e:
            err = f"LLM配置不可用：{str(e)}。请检查 config.json 和 runtime.json。"
            logger.error(err)
            return err
    api_key = os.getenv(model_config["api_key"])  # 从环境变量读取对应后端的 API key
    backend_model_name = model_config["llm_name"].split("/")[-1]
    api_base = model_config['api_base']
    return api_key,backend_model_name,api_base


