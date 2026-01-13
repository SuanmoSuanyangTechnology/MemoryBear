import asyncio
import json
from datetime import datetime
from typing import List, Optional, Tuple
from uuid import uuid4

from app.core.logging_config import get_memory_logger
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.models.base_response import RobustLLMResponse
from app.core.memory.models.graph_models import MemorySummaryNode
from app.core.memory.models.message_models import DialogData
from app.core.memory.utils.prompt.prompt_utils import render_memory_summary_prompt
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


async def generate_title_and_type_for_summary(
    content: str,
    end_user_id: str,
    llm_client
) -> Tuple[str, str]:
    """
    为MemorySummary生成标题和类型
    
    此方法应该在创建MemorySummary节点时调用，生成title和type
    
    Args:
        content: Summary的内容文本
        end_user_id: 终端用户ID (group_id)
        llm_client: LLM客户端实例
        
    Returns:
        (标题, 类型)元组
    """
    from app.core.memory.utils.prompt.prompt_utils import render_episodic_title_and_type_prompt
    
    # 定义有效的类型集合
    VALID_TYPES = {
        "conversation",      # 对话
        "project_work",      # 项目/工作
        "learning",          # 学习
        "decision",          # 决策
        "important_event"    # 重要事件
    }
    DEFAULT_TYPE = "conversation"  # 默认类型
    
    try:
        if not content:
            logger.warning("content为空，无法生成标题和类型")
            return ("空内容", DEFAULT_TYPE)
        
        # 1. 渲染Jinja2提示词模板
        prompt = await render_episodic_title_and_type_prompt(content)
        
        # 2. 调用LLM生成标题和类型
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        response = await llm_client.chat(messages=messages)
        
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
            title = result_data.get("title", "未知标题")
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
            
            logger.info(f"成功生成标题和类型: title={title}, type={episodic_type}")
            return (title, episodic_type)
            
        except json.JSONDecodeError:
            logger.error(f"无法解析LLM响应为JSON: {full_response}")
            return ("解析失败", DEFAULT_TYPE)
        
    except Exception as e:
        logger.error(f"生成标题和类型时出错: {str(e)}", exc_info=True)
        return ("错误", DEFAULT_TYPE)

async def _process_chunk_summary(
    dialog: DialogData,
    chunk,
    llm_client,
    embedder: OpenAIEmbedderClient,
) -> Optional[MemorySummaryNode]:
    """Process a single chunk to generate a memory summary node."""
    # Skip empty chunks
    if not chunk.content or not chunk.content.strip():
        return None

    try:
        # Render prompt via Jinja2 for a single chunk
        prompt_content = await render_memory_summary_prompt(
            chunk_texts=chunk.content,
            json_schema=MemorySummaryResponse.model_json_schema(),
            max_words=200,
        )

        messages = [
            {"role": "system", "content": "You are an expert memory summarizer."},
            {"role": "user", "content": prompt_content},
        ]

        # Generate structured summary with the existing LLM client
        structured = await llm_client.response_structured(
            messages=messages,
            response_model=MemorySummaryResponse,
        )
        summary_text = structured.summary.strip()

        # Generate title and type for the summary
        title = None
        episodic_type = None
        try:
            title, episodic_type = await generate_title_and_type_for_summary(
                content=summary_text,
                end_user_id=dialog.group_id,
                llm_client=llm_client
            )
            logger.info(f"Generated title and type for MemorySummary: title={title}, type={episodic_type}")
        except Exception as e:
            logger.warning(f"Failed to generate title and type for chunk {chunk.id}: {e}")
            # Continue without title and type

        # Embed the summary
        embedding = (await embedder.response([summary_text]))[0]

        # Build node per chunk
        # Note: title is stored in the 'name' field, type is stored in 'memory_type' field
        node = MemorySummaryNode(
            id=uuid4().hex,
            name=title if title else f"MemorySummaryChunk_{chunk.id}",
            group_id=dialog.group_id,
            user_id=dialog.user_id,
            apply_id=dialog.apply_id,
            run_id=dialog.run_id,  # 使用 dialog 的 run_id
            created_at=datetime.now(),
            expired_at=datetime(9999, 12, 31),
            dialog_id=dialog.id,
            chunk_ids=[chunk.id],
            content=summary_text,
            memory_type=episodic_type,
            summary_embedding=embedding,
            metadata={"ref_id": dialog.ref_id},
            config_id=dialog.config_id,  # 添加 config_id
        )
        return node

    except Exception as e:
        # Log the error but continue processing other chunks
        logger.warning(f"Failed to generate summary for chunk {chunk.id} in dialog {dialog.id}: {e}", exc_info=True)
        return None


async def memory_summary_generation(
    chunked_dialogs: List[DialogData],
    llm_client,
    embedder_client: OpenAIEmbedderClient,
) -> List[MemorySummaryNode]:
    """Generate memory summaries per chunk, embed them, and return nodes."""
    # Collect all tasks for parallel processing
    tasks = []
    for dialog in chunked_dialogs:
        for chunk in dialog.chunks:
            tasks.append(_process_chunk_summary(dialog, chunk, llm_client, embedder_client))

    # Process all chunks in parallel
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Filter out None values (failed or empty chunks)
    nodes = [node for node in results if node is not None]

    return nodes
