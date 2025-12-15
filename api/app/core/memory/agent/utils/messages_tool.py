import json
import logging
import re
from typing import List, Any

from langchain_core.messages import AnyMessage
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)


def _to_openai_messages(msgs: List[AnyMessage]) -> List[dict]:
    out = []
    for m in msgs:
        if hasattr(m, "content"):
            out.append({"role": "user", "content": getattr(m, "content", "")})
        elif isinstance(m, dict) and "role" in m and "content" in m:
            out.append(m)
        else:
            out.append({"role": "user", "content": str(m)})
    return out


def _extract_content(resp: Any) -> str:
    """Extract LLM content and sanitize to raw JSON/text.

    - Supports both object and dict response shapes.
    - Removes leading role labels (e.g., "Assistant:").
    - Strips Markdown code fences like ```json ... ```.
    - Attempts to isolate the first valid JSON array/object block when extra text is present.
    """

    def _to_text(r: Any) -> str:
        try:
            # 对象形式: resp.choices[0].message.content
            if hasattr(r, "choices") and getattr(r, "choices", None):
                msg = r.choices[0].message
                if hasattr(msg, "content"):
                    return msg.content
                if isinstance(msg, dict) and "content" in msg:
                    return msg["content"]
            # 字典形式: resp["choices"][0]["message"]["content"]
            if isinstance(r, dict):
                return r.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            pass
        return str(r)

    def _clean_text(text: str) -> str:
        s = str(text).strip()
        # 移除可能的角色前缀
        s = re.sub(r"^\s*(Assistant|assistant)\s*:\s*", "", s)
        # 提取 ```json ... ``` 代码块
        m = re.search(r"```json\s*(.*?)\s*```", s, flags=re.S | re.I)
        if m:
            s = m.group(1).strip()
        # 如果仍然包含多余文本，尝试截取第一个 JSON 数组/对象片段
        if not (s.startswith("{") or s.startswith("[")):
            left = s.find("[")
            right = s.rfind("]")
            if left != -1 and right != -1 and right > left:
                s = s[left:right + 1].strip()
            else:
                left = s.find("{")
                right = s.rfind("}")
                if left != -1 and right != -1 and right > left:
                    s = s[left:right + 1].strip()
        return s

    raw = _to_text(resp)
    return _clean_text(raw)

def Resolve_username(usermessages):
    '''
    Extract username
    Args:
        usermessages: user name

    Returns:

    '''
    usermessages = usermessages.split('_')[1:]
    sessionid = '_'.join(usermessages[:-1])
    return sessionid


# TODO: USE app.core.memory.src.utils.render_template instead
async def read_template_file(template_path: str) -> str:
    """
    读取模板文件

    Args:
        template_path: 模板文件路径

    Returns:
        模板内容字符串

    Note:
        建议使用 app.core.memory.utils.template_render 中的统一模板渲染功能
    """
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"模板文件未找到: {template_path}")
        raise
    except IOError as e:
        logger.error(f"读取模板文件失败: {template_path}, 错误: {str(e)}", exc_info=True)
        raise


async def Problem_Extension_messages_deal(context):
    '''
    Extract data
    Args:
        context:
    Returns:
    '''
    extent_quest = []
    original = context.get('original', '')
    messages = context.get('context', '')
    messages = json.loads(messages)
    for message in messages:
        question = message.get('question', '')
        type = message.get('type', '')
        extent_quest.append({"role": "user", "content": f"问题:{question}；问题类型：{type}"})

    return extent_quest, original


async def Retriev_messages_deal(context):
    '''
    Extract data
    Args:
        context:
    Returns:
    '''
    if isinstance(context, dict):
        if 'context' in context or 'original' in context:
            return context.get('context', {}), context.get('original', '')
    return content, original_value

async  def Verify_messages_deal(context):
    '''
    Extract data
    Args:
        context:
    Returns:
    '''

    query = context['context']['Query']
    Query_small_list = context['context']['Expansion_issue']
    Result_small = []
    Query_small = []
    for i in Query_small_list:
        Result_small.append(i['Answer_Small'][0])
        Query_small.append(i['Query_small'])
    return Query_small, Result_small, query


async def Summary_messages_deal(context):
    '''
    Extract data
    Args:
        context:
    Returns:
    '''
    messages = str(context).replace('\\n', '').replace('\n', '').replace('\\', '')
    query = re.findall(r'"query": (.*?),', messages)[0]
    query = query.replace('[', '').replace(']', '').strip()
    matches = re.findall(r'"answer_small"\s*:\s*"(\[.*?\])"', messages)
    answer_small_texts = []
    for m in matches:
        try:
            parsed = json.loads(m)
            for item in parsed:
                answer_small_texts.append(item.strip().replace('\\', '').replace('[', '').replace(']', ''))
        except Exception:
            answer_small_texts.append(m.strip().replace('\\', '').replace('[', '').replace(']', ''))

    return answer_small_texts, query


async def VerifyTool_messages_deal(context):
    '''
    Extract data
    Args:
        context:
    Returns:
    '''
    messages = str(context).replace('\\n', '').replace('\n', '').replace('\\', '')
    content_messages = messages.split('"context":')[1].replace('""', '"')
    messages = str(content_messages).split("name='Retrieve'")[0]
    query = re.findall('"Query": "(.*?)"', messages)[0]
    Query_small = re.findall('"Query_small": "(.*?)"', messages)
    Result_small = re.findall('"Result_small": "(.*?)"', messages)
    return Query_small, Result_small, query


async def Retrieve_Summary_messages_deal(context):
    pass


async def Retrieve_verify_tool_messages_deal(context, history, query):
    '''
    Extract data
    Args:
        context:
    Returns:
    '''
    results = []
    # 统一转为字符串，避免 None 或非字符串导致正则报错
    text = str(context)
    blocks = re.findall(r'\{(.*?)\}', text, flags=re.S)
    for block in blocks:
        query_small = re.search(r'"Query_small"\s*:\s*"([^"]*)"', block)
        answer_small = re.search(r'"Answer_Small"\s*:\s*(\[[^\]]*\])', block)
        status = re.search(r'"status"\s*:\s*"([^"]*)"', block)
        query_answer = re.search(r'"Query_answer"\s*:\s*"([^"]*)"', block)

        results.append({
            "query_small": query_small.group(1) if query_small else None,
            "answer_small": answer_small.group(1) if answer_small else None,
            # 将缺失的 status 统一为空字符串，后续用字符串判定，避免 NoneType 错误
            "status": status.group(1) if status else "",
            "query_answer": query_answer.group(1) if query_answer else None
        })
    result = []
    for r in results:
        # 统一按字符串判定状态，兼容大小写和缺失情况
        status_str = str(r.get('status', '')).strip().lower()
        if status_str == 'false':
            continue
        else:
            result.append(r)
    split_result = 'failed' if not result else 'success'
    result = {"data": {"query": query, "expansion_issue": result}, "split_result": split_result, "reason": "",
              "history": history}
    return result
