import os
from collections import defaultdict
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages

PROJECT_ROOT_ = str(Path(__file__).resolve().parents[3])

class WriteState(TypedDict):
    '''
    Langgrapg Writing TypedDict
    '''
    messages: Annotated[list[AnyMessage], add_messages]
    end_user_id: str
    errors: list[dict]  # Track errors: [{"tool": "tool_name", "error": "message"}]
    memory_config: object
    write_result: dict
    data: str
    language: str  # 语言类型 ("zh" 中文, "en" 英文)

class ReadState(TypedDict):
    """
    LangGraph 工作流状态定义

    Attributes:
        messages: 消息列表，支持自动追加
        loop_count: 遍历次数
        search_switch: 搜索类型开关
        end_user_id: 组标识
        config_id: 配置ID，用于过滤结果
        data: 从content_input_node传递的内容数据
        spit_data: 从Split_The_Problem传递的分解结果
        tool_calls: 工具调用请求列表
        tool_results: 工具执行结果列表
        memory_config: 内存配置对象
    """
    messages: Annotated[list[AnyMessage], add_messages]  # 消息追加模式
    loop_count: int
    search_switch: str
    end_user_id: str
    config_id: str
    data: str  # 新增字段用于传递内容
    spit_data: dict  # 新增字段用于传递问题分解结果
    problem_extension:dict
    storage_type: str
    user_rag_memory_id: str
    llm_id: str
    embedding_id: str
    memory_config: object  # 新增字段用于传递内存配置对象
    retrieve:dict
    RetrieveSummary: dict
    InputSummary: dict
    verify: dict
    SummaryFails: dict
    summary: dict
class COUNTState:
    """
    工作流对话检索内容计数器

    用于记录工作流对话检索内容没有正确消息召回遍历的次数。
    """

    def __init__(self, limit: int = 5):
        """
        初始化计数器

        Args:
            limit: 最大计数限制，默认为5
        """
        self.total: int = 0  # 当前累加值
        self.limit: int = limit  # 最大上限

    def add(self, value: int = 1) -> None:
        """
        累加数字，如果达到上限就保持最大值

        Args:
            value: 要累加的值，默认为1
        """
        self.total += value
        print(f"[COUNTState] 当前值: {self.total}")
        if self.total >= self.limit:
            print(f"[COUNTState] 达到上限 {self.limit}")
            self.total = self.limit  # 达到上限不再增加

    def get_total(self) -> int:
        """
        获取当前累加值

        Returns:
            当前累加值
        """
        return self.total

    def reset(self) -> None:
        """手动重置累加值"""
        self.total = 0
        print("[COUNTState] 已重置为 0")

def deduplicate_entries(entries):
    seen = set()
    deduped = []
    for entry in entries:
        key = (entry['Query_small'], entry['Result_small'])
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped

def merge_to_key_value_pairs(data, query_key, result_key):
    grouped = defaultdict(list)
    for item in data:
        grouped[item[query_key]].append(item[result_key])
    return [{key: values} for key, values in grouped.items()]


def convert_extended_question_to_question(data):
    """
    递归地将数据中的 extended_question 字段转换为 question 字段

    Args:
        data: 要转换的数据（可能是字典、列表或其他类型）

    Returns:
        转换后的数据
    """
    if isinstance(data, dict):
        # 创建新字典来存储转换后的数据
        converted = {}
        for key, value in data.items():
            if key == 'extended_question':
                # 将 extended_question 转换为 question
                converted['question'] = convert_extended_question_to_question(value)
            else:
                # 递归处理其他字段
                converted[key] = convert_extended_question_to_question(value)
        return converted
    elif isinstance(data, list):
        # 递归处理列表中的每个元素
        return [convert_extended_question_to_question(item) for item in data]
    else:
        # 其他类型直接返回
        return data