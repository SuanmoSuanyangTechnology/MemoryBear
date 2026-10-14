"""
MCP (Model Context Protocol) tools for memory read and write operations.

Exposes tools that AI models can call to store and retrieve memories
for end users. All identity and configuration is resolved server-side
by MCPAuthMiddleware — the client only provides message content.

Client configuration (all in headers, never in tool parameters):
  - Authorization: Bearer <api_key>
  - X-End-User-Other-Id: <other_id>
"""

import uuid
import warnings

from fastmcp import FastMCP

from app.controllers.service.mcp_auth_middleware import (
    mcp_config_id,
    mcp_end_user_id,
    mcp_storage_type,
    mcp_user_rag_memory_id,
    mcp_workspace_id,
)
from app.core.logging_config import get_logger
from app.core.memory.enums import SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.memory.storage_services.preference_engine.recall_service import (
    PreferenceRecallForbiddenError,
    PreferenceRecallValidationError,
    execute_preference_recall,
    masked_identifier,
)

warnings.filterwarnings("ignore", category=DeprecationWarning, module="scipy")

logger = get_logger(__name__)

mcp = FastMCP("记忆服务")


def _resolve_context() -> tuple[uuid.UUID, str, uuid.UUID, str, str]:
    """Return the memory context resolved by the middleware."""
    ws_id = mcp_workspace_id.get()
    eu_id = mcp_end_user_id.get()
    cfg_id = mcp_config_id.get()
    st = mcp_storage_type.get()
    rag_id = mcp_user_rag_memory_id.get()
    if ws_id is None or eu_id is None or cfg_id is None or st is None:
        raise RuntimeError("未提供有效的 API Key、X-End-User-Other-Id 或记忆配置")
    return ws_id, eu_id, cfg_id, st, rag_id


@mcp.tool
async def write_memory(
        message: str,
) -> dict:
    """将用户的重要信息持久化存储，供后续对话召回。

    应在以下场景调用：
    - 用户明确表达了偏好、习惯、计划或个人信息
    - 对话中出现了值得在未来参考的事实、决定或背景

    写入内容应简洁、结构化，只包含关键信息，避免冗余。
    例如："用户喜欢喝冰美式咖啡，不加糖" 比 "用户在今天的对话中提到他喜欢喝咖啡，
    具体来说是冰美式，而且不加糖" 更好。

    Args:
        message: 需要存储的记忆内容，用自然语言描述即可。
    """
    try:
        workspace_id, end_user_id, config_id, storage_type, user_rag_memory_id = _resolve_context()
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    try:
        from datetime import datetime, timezone
        dialog_at = datetime.now(timezone.utc).isoformat()

        msg_id = await MemoryService.dispatch_mcp_write(
            message=message,
            end_user_id=end_user_id,
            config_id=config_id,
            workspace_id=str(workspace_id),
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            dialog_at=dialog_at,
        )

        logger.info(f"MCP write_memory dispatched: msg_id={msg_id}, end_user={end_user_id}")
        return {"success": True, "msg_id": msg_id}
    except Exception as e:
        logger.error(f"MCP write_memory failed for end_user={end_user_id}: {e}")
        return {"success": False, "error": str(e)}


@mcp.tool
async def read_memory(
        message: str,
        search_switch: str = "express",
) -> dict:
    """检索与当前上下文相关的历史记忆。

    应在以下场景调用：
    - 开始对话或切换话题时，先回忆用户之前的偏好和背景
    - 用户提问涉及之前聊过的话题，需要获取历史上下文
    - 需要确认用户之前提过的信息，避免重复询问

    返回结果为自然语言文本，可直接用于辅助生成回复。

    Args:
        message: 检索查询，用自然语言描述想查找什么。越具体效果越好。
                 例如："用户对咖啡有什么偏好"、"上次讨论的旅行计划"。
        search_switch: - "deep"=深度检索+交叉验证（适合复杂问题）
                       - "normal"=深度检索（适合一般回忆）
                       - "quick"=快速混合检索（适合简单查询）
                       - "express"=极速关键词检索（默认，不拆解/不汇总，适合简单查询）
                       - "meta"=元数据检索（仅返回用户画像、偏好、习惯，适合用户档案类问题）
    """
    try:
        workspace_id, end_user_id, config_id, storage_type, user_rag_memory_id = _resolve_context()
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    strategy = SearchStrategy(search_switch)

    try:
        service = MemoryService(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=str(workspace_id),
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )
        result = await service.read(
            query=message,
            search_switch=strategy,
            record_display=True,
        )
        logger.info(f"MCP read_memory succeeded for end_user={end_user_id}")
        return {
            "success": True,
            "content": result.content,
            "count": result.count,
        }
    except Exception as e:
        logger.error(f"MCP read_memory failed for end_user={end_user_id}: {e}")
        return {"success": False, "error": str(e)}


@mcp.tool
async def recall_coding_preferences(
    current_user_message: str,
    context_messages: list[dict] | None = None,
) -> dict:
    """根据当前用户请求召回与本次 Coding Agent 任务相关的历史偏好。

    处理新的用户请求前调用。current_user_message 是分类主依据；
    context_messages 只用于理解指代、上下文对象和任务承接。
    返回内容是历史偏好参考数据，不得覆盖当前用户明确要求、项目客观约束以及
    系统、安全和权限规则。

    Args:
        current_user_message: 本次待处理的非空 user 消息。
        context_messages: 当前消息之前按时间正序排列的近期 user/assistant 消息。
    """
    try:
        workspace_id, end_user_id, config_id, _, _ = _resolve_context()
    except RuntimeError:
        return {"success": False, "error": "偏好召回服务暂时不可用"}

    try:
        preferences = await execute_preference_recall(
            config_id=config_id,
            workspace_id=workspace_id,
            end_user_id=end_user_id,
            request_payload={
                "current_user_message": current_user_message,
                "context_messages": [] if context_messages is None else context_messages,
            },
        )
    except PreferenceRecallValidationError:
        return {"success": False, "error": "偏好召回请求参数不合法"}
    except PreferenceRecallForbiddenError:
        return {"success": False, "error": "无权访问该偏好资源"}
    except Exception:
        logger.exception(
            "MCP preference recall failed workspace_id=%s end_user_id=%s",
            masked_identifier(str(workspace_id)),
            masked_identifier(end_user_id),
        )
        return {"success": False, "error": "偏好召回服务暂时不可用"}
    return {
        "success": True,
        "preferences": [item.model_dump(mode="json") for item in preferences],
        "count": len(preferences),
    }


mcp_app = mcp.http_app(path="/memory", transport="streamable-http", stateless_http=True)
