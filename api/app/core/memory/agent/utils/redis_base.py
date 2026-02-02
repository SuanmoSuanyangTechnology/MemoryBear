import json
from typing import Any, List, Dict, Optional
from datetime import datetime, timedelta


def serialize_messages(messages: Any) -> str:
    """
    将消息序列化为 JSON 字符串，支持 LangChain 消息对象

    Args:
        messages: 可以是 list、dict、string 或 LangChain 消息对象列表

    Returns:
        str: JSON 字符串
    """
    if isinstance(messages, str):
        return messages

    if isinstance(messages, (list, tuple)):
        # 检查是否是 LangChain 消息对象列表
        serialized_list = []
        for msg in messages:
            if hasattr(msg, 'type') and hasattr(msg, 'content'):
                # LangChain 消息对象
                serialized_list.append({
                    'type': msg.type,
                    'content': msg.content,
                    'role': getattr(msg, 'role', msg.type)
                })
            elif isinstance(msg, dict):
                serialized_list.append(msg)
            else:
                serialized_list.append(str(msg))
        return json.dumps(serialized_list, ensure_ascii=False)

    if isinstance(messages, dict):
        return json.dumps(messages, ensure_ascii=False)

    # 其他类型转为字符串
    return str(messages)


def deserialize_messages(messages_str: str) -> Any:
    """
    将 JSON 字符串反序列化为原始格式

    Args:
        messages_str: JSON 字符串

    Returns:
        反序列化后的对象（list、dict 或 string）
    """
    if not messages_str:
        return []

    try:
        return json.loads(messages_str)
    except (json.JSONDecodeError, TypeError):
        return messages_str


def fix_encoding(text: str) -> str:
    """
    修复错误编码的文本
    
    Args:
        text: 需要修复的文本
        
    Returns:
        str: 修复后的文本
    """
    if not text or not isinstance(text, str):
        return text
    try:
        # 尝试修复 Latin-1 误编码为 UTF-8 的情况
        return text.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        # 如果修复失败，返回原文本
        return text


def format_session_data(data: Dict[str, Any], include_time: bool = False) -> Dict[str, Any]:
    """
    格式化会话数据为统一的输出格式
    
    Args:
        data: 原始会话数据
        include_time: 是否包含时间字段
        
    Returns:
        Dict: 格式化后的数据 {"Query": "...", "Answer": "...", "starttime": "..."}
    """
    result = {
        "Query": fix_encoding(data.get('messages', '')),
        "Answer": fix_encoding(data.get('aimessages', ''))
    }
    
    if include_time:
        result["starttime"] = data.get('starttime', '')
    
    return result


def filter_by_time_range(items: List[Dict], minutes: int) -> List[Dict]:
    """
    根据时间范围过滤数据
    
    Args:
        items: 包含 starttime 字段的数据列表
        minutes: 时间范围（分钟）
        
    Returns:
        List[Dict]: 过滤后的数据列表
    """
    time_threshold = datetime.now() - timedelta(minutes=minutes)
    time_threshold_str = time_threshold.strftime("%Y-%m-%d %H:%M:%S")
    
    filtered_items = []
    for item in items:
        starttime = item.get('starttime', '')
        if starttime and starttime >= time_threshold_str:
            filtered_items.append(item)
    
    return filtered_items


def sort_and_limit_results(items: List[Dict], limit: int = 6, 
                           remove_time: bool = True) -> List[Dict]:
    """
    对结果进行排序、限制数量并移除时间字段
    
    Args:
        items: 数据列表
        limit: 最大返回数量
        remove_time: 是否移除 starttime 字段
        
    Returns:
        List[Dict]: 处理后的数据列表
    """
    # 按时间降序排序（最新的在前）
    items.sort(key=lambda x: x.get('starttime', ''), reverse=True)
    
    # 限制数量
    result_items = items[:limit]
    
    # 移除 starttime 字段
    if remove_time:
        for item in result_items:
            item.pop('starttime', None)
    
    # 如果结果少于1条，返回空列表
    if len(result_items) < 1:
        return []
    
    return result_items


def generate_session_key(session_id: str, key_type: str = "session") -> str:
    """
    生成 Redis key
    
    Args:
        session_id: 会话ID
        key_type: key 类型 ("session", "read", "write", "count")
        
    Returns:
        str: Redis key
    """
    if key_type == "count":
        return f"session:count:{session_id}"
    elif key_type == "write":
        return f"session:write:{session_id}"
    elif key_type == "session" or key_type == "read":
        return f"session:{session_id}"
    else:
        return f"session:{session_id}"


def get_current_timestamp() -> str:
    """
    获取当前时间戳字符串
    
    Returns:
        str: 格式化的时间字符串 "YYYY-MM-DD HH:MM:SS"
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")