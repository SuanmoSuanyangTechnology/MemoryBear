"""
集群（Multi-Agent）联调 Mock —— 自包含路由模块，可整体移除。
==================================================================
TODO(联调Mock，真实功能上线后整体移除)

【上传范围】前端工程师联调只需：
  - app/mocks/cluster_mock.py + app/mocks/__init__.py
  - main.py 增加一行（见文件尾「接入方式」）
无需任何集群真实代码（multi_agent_service / multi_agent_schema /
multi_agent_controller 均不 import、不依赖）。

- 硬编码、无环境变量开关：命中端点直接返回 Mock。
- 仅进程内内存保存 PUT 的配置（重启还原默认），不连业务库 / 模型。
- 数据形状与《docs/mock/cluster-mock-api.md》严格一致（已压缩，无完整实体）。
- SSE 按固定脚本推送，事件间 100–300ms，模拟真实流式节奏。
- 鉴权：复用平台公共依赖 get_current_user_async（JWT），非集群代码。

移除方式：删 app/mocks + 删 main.py 的 include 行（搜索 联调Mock）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.dependencies import get_current_user_async
from app.core.response_utils import success


_CONFIG_STORE: Dict[str, Dict[str, Any]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _uuid() -> str:
    return str(uuid.uuid4())


def default_config(app_id: str) -> Dict[str, Any]:
    """与接口文档 §3 同构的默认集群配置（裸主管）。"""
    return {
        "id": _uuid(),
        "app_id": app_id,
        "master_agent_id": None,
        "master_agent_name": None,
        "default_model_config_id": None,
        "model_parameters": None,
        "orchestration_mode": "supervisor_loop",
        "sub_agents": [],
        "routing_rules": None,
        "execution_config": {
            "max_iterations": 5,
            "timeout": 60,
            "stream_idle_timeout": 300,
            "parallel_limit": 3,
            "retry_on_failure": False,
            "max_retries": 2,
            "enable_rule_fast_path": False,
            "result_merge_mode": "master",
            "merge_max_tokens": 8192,
            "supervisor_max_tool_calls": 3,
            "sub_agent_execution_mode": "parallel",
        },
        "supervisor_config": None,
        "aggregation_strategy": "merge",
        "is_active": True,
        "created_at": _now_ms(),
        "updated_at": _now_ms(),
    }


def get_config(app_id: str) -> Dict[str, Any]:
    """读取：未配置则返回默认配置（不写入存储，模拟"默认模板"语义）。"""
    return _CONFIG_STORE.get(app_id) or default_config(app_id)


def save_config(app_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存：在默认配置上覆盖入参字段并回显；未提交（None）字段不更新。"""
    existing = _CONFIG_STORE.get(app_id) or default_config(app_id)
    merged = dict(existing)
    for key, value in (payload or {}).items():
        if value is not None:
            merged[key] = value
    merged["app_id"] = app_id
    merged["orchestration_mode"] = merged.get("orchestration_mode") or "supervisor_loop"
    merged["updated_at"] = _now_ms()
    merged["created_at"] = existing.get("created_at") or _now_ms()
    _CONFIG_STORE[app_id] = merged
    return merged


# ---------------------------------------------------------------------------
# SSE 帧工具
# ---------------------------------------------------------------------------
def _frame(event: str, data: Optional[Dict[str, Any]] = None) -> str:
    return f"event: {event}\ndata: {json.dumps(data or {}, ensure_ascii=False)}\n\n"


async def _frames(
    events: list[tuple[str, Optional[Dict[str, Any]]]],
    gap: float = 0.18,
) -> AsyncGenerator[str, None]:
    """按固定节奏依次下发帧。gap 取 100–300ms（默认 180ms）。"""
    for event, data in events:
        await asyncio.sleep(gap)
        yield _frame(event, data)


def _classify(message: str) -> str:
    msg = (message or "").lower()
    if "致命" in message:
        return "fatal"
    if any(k in message for k in ("失败", "报错")) or "error" in msg:
        return "fail"
    if any(k in message for k in ("自答", "直接")):
        return "direct"
    # 主管调用自有工具（正确路径）：tool_start -> tool_end
    if "工具" in message or "tool" in msg or "查询" in message:
        return "tool"
    return "dispatch"


def _get_agent_info(app_id: str) -> tuple[str, str]:
    """从进程内配置取首个子 Agent（agent_id, name），无则用假值。"""
    subs = (_CONFIG_STORE.get(app_id) or {}).get("sub_agents") or []
    if subs:
        first = subs[0]
        return str(first.get("agent_id") or _uuid()), str(first.get("name") or "Skill")
    return _uuid(), "Skill"


async def cluster_sse_stream(app_id: str, req: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """集群调试 SSE：按 message 关键字选择脚本。"""
    message = str(req.get("message") or "")
    conversation_id = req.get("conversation_id") or _uuid()
    message_id = _uuid()
    user_message_id = _uuid()
    scene = _classify(message)

    agent_id, agent_name = _get_agent_info(app_id)
    execution_id = _uuid()
    base_owner: Dict[str, Any] = {
        "execution_id": execution_id,
        "parent_execution_id": None,
        "orchestration_mode": "supervisor_loop",
        "agent_id": agent_id,
    }

    start = _frame("start", {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "user_message_id": user_message_id,
    })
    end = _frame("end", {})

    if scene == "fatal":
        yield start
        await asyncio.sleep(0.2)
        yield _frame("error", {"error": {"message": "（Mock）主管遭遇致命错误，整条流失败。"}})
        await asyncio.sleep(0.1)
        yield end
        return

    if scene == "direct":
        yield start
        async for fr in _frames([
            ("message", {"content": "这是主管直接给出的回答，"}),
            ("message", {"content": "无需派单，已为你处理完成。"}),
        ]):
            yield fr
        yield end
        return

    if scene == "fail":
        yield start
        async for fr in _frames([
            ("agent_dispatch", {**base_owner, "agent_name": agent_name, "task": message}),
            ("error", {**base_owner, "error": {"message": "子 Agent 执行失败（Mock 错误路径）"}}),
        ]):
            yield fr
        yield end
        return

    if scene == "tool":
        # 主管调用自有工具（正确路径）：start -> message -> tool_start -> tool_end -> message -> end
        step_id = _uuid()
        tool_input = {"query": message}
        yield start
        async for fr in _frames([
            ("message", {"content": "好的，我来调用工具查询。"}),
            ("tool_start", {
                "step_id": step_id,
                "name": "数据库查询",
                "input": tool_input,
                "meta": {"tool_id": "3b5cc4d0-06f2-4aba-bc9d-3859168572d7"},
            }),
            ("tool_end", {
                "step_id": step_id,
                "output": {"rows": [
                    {"id": 1, "name": "示例结果 A"},
                    {"id": 2, "name": "示例结果 B"},
                ], "row_count": 2},
            }),
            ("message", {"content": "工具调用完成，以上是查询结果。"}),
        ]):
            yield fr
        yield end
        return

    yield start
    async for fr in _frames([
        ("message", {"content": "好的，我来安排专业的子 Agent 处理。"}),
        ("agent_dispatch", {**base_owner, "agent_name": agent_name, "task": message}),
        ("agent_log", {**base_owner, "data": {"iterations": [
            {"llm": {"output": "子 Agent 思考中…"}}]}}),
        ("agent_log_final", {**base_owner, "data": {"iterations": [
            {"llm": {"output": "子 Agent 处理完成"}}]}}),
        ("agent_complete", {
            **base_owner,
            "status": "success",
            "output": "这是子 Agent 的处理结果",
            "elapsed_time": 0.82,
            "token_usage": {"prompt_tokens": 120, "completion_tokens": 64, "total_tokens": 184},
        }),
    ]):
        yield fr
    yield end


# ---------------------------------------------------------------------------
# 自包含路由：三个端点全部在此，不经过任何集群 controller / service。
# 注意：必须在真实集群路由之前注册（路径相同，先匹配者生效）。
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/apps", tags=["Mock-Cluster"])


@router.get("/{app_id}/multi-agent", summary="[Mock] 读取集群配置")
async def _mock_get_multi_agent(
    app_id: str,
    _user: Any = Depends(get_current_user_async),
):
    return success(data=get_config(str(app_id)))


@router.put("/{app_id}/multi-agent", summary="[Mock] 保存集群配置")
async def _mock_put_multi_agent(
    app_id: str,
    payload: Dict[str, Any],
    _user: Any = Depends(get_current_user_async),
):
    return success(data=save_config(str(app_id), payload), msg="多 Agent 配置更新成功")


@router.post("/{app_id}/draft/run", summary="[Mock] 集群调试运行（stream=true 走 SSE）")
async def _mock_draft_run(
    app_id: str,
    payload: Dict[str, Any],
    _user: Any = Depends(get_current_user_async),
):
    req = payload or {}
    if req.get("stream"):
        return StreamingResponse(
            cluster_sse_stream(str(app_id), req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return success(data={"response": "（Mock）这是主管给出的结果。"})


# ===========================================================================
# 接入方式（main.py 中，在现有路由注册之前加这一行）：
#
#   # TODO(联调Mock，真实功能上线后整体移除)
#   from app.mocks import cluster_mock
#   app.include_router(cluster_mock.router, prefix="/api")
#
# 必须位于 `app.include_router(manager_router, prefix="/api")` 之前，
# 使 Mock 的同路径端点优先匹配。
# ===========================================================================
