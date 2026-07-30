import asyncio
import json
from datetime import datetime
from typing import Any, List, Optional, Tuple
from uuid import uuid4

from app.core.utils.datetime_utils import utcnow_naive
from app.core.logging_config import get_memory_logger

from app.core.memory.models.base_response import RobustLLMResponse
from app.core.memory.models.graph_models import MemorySummaryNode
from app.core.memory.models.message_models import DialogData
from app.core.memory.utils.prompt.prompt_utils import render_memory_summary_prompt # 相关内容也需要删除
from app.core.memory.utils.prompt.prompt_utils import render_neo4j_memory_summary_prompt
from app.core.language_utils import validate_language  # 使用集中化的语言校验
from pydantic import Field

logger = get_memory_logger(__name__)


class MemorySummaryResponse(RobustLLMResponse):
    """Structured response for summary generation per chunk.

    This model ensures the LLM returns a valid, non-empty summary.
    Inherits robust validation from RobustLLMResponse.
    """
    summary: str = Field(
        ...,
        description="Concise memory summary for a single chunk. Must be a meaningful, non-empty string.",
        min_length=1,
        max_length=5000
    )


class EpisodicCombinedResponse(RobustLLMResponse):
    """Combined response: title + type + summary in one LLM call.

    Used by neo4j_memory_summary.jinja2 prompt to reduce per-chunk LLM calls from 2 to 1.
    """

    title: str = Field(
        ...,
        description="Concise title capturing the core subject or event.",
        min_length=1,
        max_length=200,
    )
    type: str = Field(
        ...,
        description="Episodic memory type classification (conversation/project_work/learning/decision/important_event).",
    )
    summary: str = Field(
        ...,
        description="Faithful summary of the input chunk.",
        min_length=1,
        max_length=5000,
    )


async def generate_title_and_type_for_summary( # 只作用于遗忘，已经有新的任务替代遗忘的作用，确定后准备清理
    content: str,
    llm_client,
    language: str = "zh"
) -> Tuple[str, str]:
    """
    为MemorySummary生成标题和类型
    
    此方法应该在创建MemorySummary节点时调用，生成title和type
    
    Args:
        content: Summary的内容文本
        llm_client: LLM客户端实例
        language: 生成标题使用的语言 ("zh" 中文, "en" 英文)，默认中文
        
    Returns:
        (标题, 类型)元组
    """
    from app.core.memory.utils.prompt.prompt_utils import render_episodic_title_and_type_prompt
    
    # 验证语言参数
    language = validate_language(language)
    
    # 定义有效的类型集合
    VALID_TYPES = {
        "conversation",      # 对话
        "project_work",      # 项目/工作
        "learning",          # 学习
        "decision",          # 决策
        "important_event"    # 重要事件
    }
    DEFAULT_TYPE = "conversation"  # 默认类型
    
    # 根据语言设置默认标题
    DEFAULT_TITLE = "空内容" if language == "zh" else "Empty Content"
    PARSE_ERROR_TITLE = "解析失败" if language == "zh" else "Parse Failed"
    ERROR_TITLE = "错误" if language == "zh" else "Error"
    UNKNOWN_TITLE = "未知标题" if language == "zh" else "Unknown Title"
    
    try:
        if not content:
            logger.warning(f"content为空，无法生成标题和类型 (language={language})")
            return (DEFAULT_TITLE, DEFAULT_TYPE)
        
        # 1. 渲染Jinja2提示词模板，传递语言参数
        prompt = await render_episodic_title_and_type_prompt(content, language=language)
        
        # 2. 调用LLM生成标题和类型
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        response = await llm_client.ainvoke(messages)
        
        # 3. 解析LLM响应
        content_response = response.content
        if isinstance(content_response, list):
            if len(content_response) > 0:
                if isinstance(content_response[0], dict):
                    text = content_response[0].get('text', content_response[0].get('content', str(content_response[0])))
                    full_response = str(text)
                else:
                    full_response = str(content_response[0])
            else:
                full_response = ""
        elif isinstance(content_response, dict):
            full_response = str(content_response.get('text', content_response.get('content', str(content_response))))
        else:
            full_response = str(content_response) if content_response is not None else ""
        
        # 4. 解析JSON响应
        try:
            # 尝试从响应中提取JSON
            # 移除可能的markdown代码块标记
            json_str = full_response.strip()
            if json_str.startswith("```json"):
                json_str = json_str[7:]
            if json_str.startswith("```"):
                json_str = json_str[3:]
            if json_str.endswith("```"):
                json_str = json_str[:-3]
            json_str = json_str.strip()
            
            result_data = json.loads(json_str)
            title = result_data.get("title", UNKNOWN_TITLE)
            episodic_type_raw = result_data.get("type", DEFAULT_TYPE)
            
            # 5. 校验和归一化类型
            # 将类型转换为小写并去除空格
            episodic_type_normalized = str(episodic_type_raw).lower().strip()
            
            # 检查是否在有效类型集合中
            if episodic_type_normalized in VALID_TYPES:
                episodic_type = episodic_type_normalized
            else:
                # 尝试映射常见的中文类型到英文
                type_mapping = {
                    "对话": "conversation",
                    "项目": "project_work",
                    "工作": "project_work",
                    "项目/工作": "project_work",
                    "学习": "learning",
                    "决策": "decision",
                    "重要事件": "important_event",
                    "事件": "important_event"
                }
                episodic_type = type_mapping.get(episodic_type_raw, DEFAULT_TYPE)
                logger.warning(
                    f"LLM返回的类型 '{episodic_type_raw}' 不在有效集合中，"
                    f"已归一化为 '{episodic_type}'"
                )
            
            logger.debug(f"成功生成标题和类型 (language={language}): title={title}, type={episodic_type}")
            return (title, episodic_type)
            
        except json.JSONDecodeError:
            logger.error(f"无法解析LLM响应为JSON (language={language}): {full_response}")
            return (PARSE_ERROR_TITLE, DEFAULT_TYPE)
        
    except Exception as e:
        logger.error(f"生成标题和类型时出错 (language={language}): {str(e)}", exc_info=True)
        return (ERROR_TITLE, DEFAULT_TYPE)


_SUMMARY_MAX_RETRIES = 2

_VALID_EPISODIC_TYPES = {"conversation", "project_work", "learning", "decision", "important_event"}

_EPISODIC_TYPE_MAPPING = {
    "对话": "conversation",
    "项目": "project_work",
    "工作": "project_work",
    "项目/工作": "project_work",
    "学习": "learning",
    "决策": "decision",
    "重要事件": "important_event",
    "事件": "important_event",
}


async def memory_summary_generation(
    chunked_dialogs: List[DialogData],
    llm_client,
    embedder_client: Any,
    language: str = "zh",
) -> List[MemorySummaryNode]:
    """Generate memory summaries per chunk with retry, embed them, and return nodes.

    每个 chunk 通过单次 LLM 调用 (neo4j_memory_summary.jinja2) 同时产出
    title、type、summary，失败时指数退避重试。

    Args:
        chunked_dialogs: 分块后的对话数据
        llm_client: LLM 客户端
        embedder_client: 嵌入客户端
        language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
    """
    language = validate_language(language)

    async def _process_chunk(dialog: DialogData, chunk) -> Optional[MemorySummaryNode]:
        """处理单个 chunk：渲染 prompt → LLM → embed → 构建节点，带重试。"""
        if not chunk.content or not chunk.content.strip():
            return None

        for attempt in range(1, _SUMMARY_MAX_RETRIES + 1):
            try:
                # 渲染合并提示词
                prompt_content = await render_neo4j_memory_summary_prompt(
                    chunk_texts=chunk.content,
                    max_words=200,
                    language=language,
                )

                # 单次 LLM 调用产出 title / type / summary
                structured: EpisodicCombinedResponse = await llm_client.call_structured(
                    [
                        {"role": "system", "content": "You are an expert memory summarizer."},
                        {"role": "user", "content": prompt_content},
                    ],
                    EpisodicCombinedResponse,
                )

                summary_text = structured.summary.strip()
                if not summary_text:
                    return None

                # 归一化 type
                episodic_type = structured.type.lower().strip()
                if episodic_type not in _VALID_EPISODIC_TYPES:
                    episodic_type = _EPISODIC_TYPE_MAPPING.get(episodic_type, "conversation")

                # 生成 embedding
                embedding = (await embedder_client.aembed_documents([summary_text]))[0]

                return MemorySummaryNode(
                    id=uuid4().hex,
                    name=structured.title or f"MemorySummaryChunk_{chunk.id}",
                    end_user_id=dialog.end_user_id,
                    user_id=dialog.end_user_id,
                    apply_id=dialog.end_user_id,
                    run_id=dialog.run_id,
                    created_at=utcnow_naive(),
                    dialog_id=dialog.id,
                    chunk_ids=[chunk.id],
                    content=summary_text,
                    memory_type=episodic_type,
                    summary_embedding=embedding,
                    metadata={"ref_id": dialog.ref_id},
                    config_id=dialog.config_id,
                )

            except Exception as e:
                if attempt == _SUMMARY_MAX_RETRIES:
                    logger.warning(
                        "Chunk %s in dialog %s summary failed after %d attempts: %s",
                        chunk.id, dialog.id, _SUMMARY_MAX_RETRIES, e,
                        exc_info=True,
                    )
                    return None
                await asyncio.sleep(0.5 * attempt)

    # 并发处理所有 chunk
    results = await asyncio.gather(
        *[
            _process_chunk(dialog, chunk)
            for dialog in chunked_dialogs
            for chunk in dialog.chunks
        ],
        return_exceptions=False,
    )

    return [node for node in results if node is not None]
