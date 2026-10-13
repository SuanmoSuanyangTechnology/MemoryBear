"""
集群（Multi-Agent）补充功能联调 Mock —— 自包含路由模块，可整体移除。
==================================================================
TODO(联调Mock，真实功能上线后整体移除)

【上传范围】前端工程师联调只需：
  - app/mocks/cluster_mock.py + app/mocks/__init__.py
  - main.py 增加两行（见文件尾「接入方式」）
无需任何集群真实代码（multi_agent_service / multi_agent_schema /
multi_agent_controller / app_controller 均不 import、不依赖）。

- 硬编码、无环境变量开关：命中端点直接返回 Mock。
- 进程内内存保存 PUT 的配置 / 版本 / 收藏 / 反馈状态（重启还原默认），不连业务库 / 模型。
- 数据形状与《docs/mock/cluster-mock-api.md》严格一致。
- SSE 按固定脚本推送，事件间 100–300ms，模拟真实流式节奏。
- 鉴权：复用平台公共依赖 get_current_user_async（JWT），非集群代码。

本 Mock 覆盖（均为线上尚无的集群新端点；不覆盖平台核心路由）：
  1. GET / PUT /apps/{app_id}/multi-agent        集群配置读写
  2. POST /apps/{app_id}/draft/run               调试运行（stream=true 走 SSE）
  3. POST /apps/{app_id}/messages/{mid}/regenerate    重新生成（流式+非流式，多版本递增）
  4. GET  /apps/{app_id}/messages/{mid}/versions      版本列表
  5. POST /apps/{app_id}/messages/{mid}/switch-version/{v}/branch  切版本+分支链
  6. POST /apps/{app_id}/messages/{mid}/favorite      收藏/取消收藏（幂等）
  7. POST/GET /apps/{app_id}/messages/{mid}/feedback  点赞/点踩（幂等）

【不 mock、走线上真实接口】：
  - SSO/普通入口创建集群 = POST /api/apps（body type=multi_agent），线上 create_app 已支持
  - 工作流引用集群的选择器 = GET /api/apps?type=multi_agent&orchestration_mode=supervisor_loop，
    线上 list_apps 已有 orchestration_mode 过滤参数（app_controller.py:451）
  原因：实测 FastAPI 对同路径同步真实路由会优先生效（穿透不可控），mock 核心路由有线上风险。

移除方式：删 app/mocks + 删 main.py 的 include 两行（搜索 联调Mock）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.dependencies import get_current_user_async
from app.core.response_utils import success
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException


# ---------------------------------------------------------------------------
# 进程内存储（重启还原默认；不连业务库）
# ---------------------------------------------------------------------------
_CONFIG_STORE: Dict[str, Dict[str, Any]] = {}
# 集群应用（SSO/普通入口创建）：app_id -> 应用对象
_CLUSTER_APP_STORE: Dict[str, Dict[str, Any]] = {}
# 消息版本：app_id+parent_user_msg -> {"versions": [...], "current_version": int}
# 每条消息：message_id / version / is_current / content / created_at
_MESSAGE_VERSION_STORE: Dict[str, Dict[str, Any]] = {}
# 收藏：message_id -> bool
_FAVORITE_STORE: Dict[str, bool] = {}
# 反馈：message_id -> feedback_type(like/dislike) or None
_FEEDBACK_STORE: Dict[str, Optional[str]] = {}
# 标注：app_id -> {"setting": {...}, "items": [ {id,question,answer,hit_count,...} ]}
_ANNOTATION_STORE: Dict[str, Dict[str, Any]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 集群配置（沿用上一版：GET/PUT /apps/{app_id}/multi-agent）
# ---------------------------------------------------------------------------
def default_config(app_id: str) -> Dict[str, Any]:
    """与接口文档 §3 同构的默认集群配置（裸主管）。"""
    return {
        "id": _uuid(),
        "app_id": app_id,
        "master_agent_id": None,
        "master_agent_name": None,
        "default_model_config_id": None,
        # 与文档 §3.5 同构的默认模型参数（前端模型配置弹窗直接回填此形状）：
        # deep_thinking/json_output 默认关；features 声明能力，驱动弹窗开关可见性
        "model_parameters": {
            "temperature": 0.7,
            "max_tokens": 2000,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "n": 1,
            "stop": None,
            "deep_thinking": False,
            "thinking_budget_tokens": 1000,
            "json_output": False,
            "features": ["thinking", "json_output", "function_call"],
        },
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
    return _CONFIG_STORE.get(app_id) or default_config(app_id)


def save_config(app_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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
    events: List[tuple[str, Optional[Dict[str, Any]]]],
    gap: float = 0.18,
) -> AsyncGenerator[str, None]:
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
    if "工具" in message or "tool" in msg or "查询" in message:
        return "tool"
    return "dispatch"


def _get_agent_info(app_id: str) -> tuple[str, str]:
    subs = (_CONFIG_STORE.get(app_id) or {}).get("sub_agents") or []
    if subs:
        first = subs[0]
        return str(first.get("agent_id") or _uuid()), str(first.get("name") or "Skill")
    return _uuid(), "Skill"


# ---------------------------------------------------------------------------
# 功能特性辅助（读取已 PUT 的 supervisor_config.features / model_parameters）
# ---------------------------------------------------------------------------
def _get_features(app_id: str) -> Dict[str, Any]:
    """取已保存配置里的 supervisor_config.features（缺省空 dict=全关）。"""
    cfg = _CONFIG_STORE.get(app_id) or {}
    sup = cfg.get("supervisor_config") or {}
    feats = sup.get("features")
    return feats if isinstance(feats, dict) else {}


def _feat_on(features: Dict[str, Any], key: str) -> bool:
    sub = features.get(key)
    return isinstance(sub, dict) and bool(sub.get("enabled"))


def _deep_thinking_on(app_id: str, req: Dict[str, Any]) -> bool:
    """深度思考 = model_parameters.deep_thinking 且请求 thinking=true 同开。"""
    cfg = _CONFIG_STORE.get(app_id) or {}
    mp = cfg.get("model_parameters") or {}
    mp_on = bool(mp.get("deep_thinking")) if isinstance(mp, dict) else False
    return mp_on and bool(req.get("thinking"))


def _json_output_on(app_id: str) -> bool:
    """JSON 输出 = model_parameters.json_output（纯配置开关，无需请求侧参数）。"""
    cfg = _CONFIG_STORE.get(app_id) or {}
    mp = cfg.get("model_parameters") or {}
    return bool(mp.get("json_output")) if isinstance(mp, dict) else False


# JSON 输出开启时，主管/子 Agent 正文换成 JSON 字符串（模拟结构化输出）
_JSON_SUPERVISOR_1 = '{"answer": "这是主管直接给出的结构化回答",'
_JSON_SUPERVISOR_2 = '"confidence": 0.95, "sources": ["mock"]}'
_JSON_SUB_OUTPUT = '{"result": "这是子 Agent 的结构化处理结果", "status": "ok"}'


def _get_features(app_id: str) -> Dict[str, Any]:
    """取已保存配置里的 supervisor_config.features（缺省空 dict=全关）。"""
    cfg = _CONFIG_STORE.get(app_id) or {}
    sup = cfg.get("supervisor_config") or {}
    feats = sup.get("features")
    return feats if isinstance(feats, dict) else {}


def _feat_on(features: Dict[str, Any], key: str) -> bool:
    sub = features.get(key)
    return isinstance(sub, dict) and bool(sub.get("enabled"))


def _opening_frames(app_id: str, req: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    """新会话首轮且开场白开启时，先给开场白 message 帧（{{var}} 用 variables 替换）。"""
    features = _get_features(app_id)
    if req.get("conversation_id"):  # 续传会话 = 非首轮，无开场白
        return []
    if not _feat_on(features, "opening_statement"):
        return []
    oc = features.get("opening_statement") or {}
    statement = oc.get("statement") or ""
    if not statement:
        return []
    variables = req.get("variables") or {}
    for k, v in variables.items():
        statement = statement.replace("{{%s}}" % k, str(v)).replace("{{ %s}}" % k, str(v))
    return [("message", {"content": statement})]


def _end_extras(app_id: str) -> Dict[str, Any]:
    """end 事件附加字段（仅对应特性开启才有键，缺省空 dict）。"""
    features = _get_features(app_id)
    extras: Dict[str, Any] = {}
    if _feat_on(features, "suggested_questions_after_answer"):
        extras["suggested_questions"] = ["再问我一个问题", "换个角度解释", "给些示例"]
    if _feat_on(features, "citation"):
        extras["citations"] = [{
            "document_id": _uuid(), "doc_id": _uuid(),
            "file_name": "示例知识文档.pdf", "knowledge_id": _uuid(),
            "score": 0.92, "download_url": None,
        }]
    if _feat_on(features, "text_to_speech"):
        extras["audio_url"] = "https://example.com/mock/tts.mp3"
        extras["audio_status"] = "completed"
    return extras


async def cluster_sse_stream(app_id: str, req: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """集群调试 SSE：按 message 关键字选择脚本；功能特性按已保存配置生效。"""
    message = str(req.get("message") or "")
    conversation_id = req.get("conversation_id") or _uuid()
    is_new = not req.get("conversation_id")
    message_id = _uuid()
    user_message_id = _uuid()
    scene = _classify(message)
    deep_thinking = _deep_thinking_on(app_id, req)

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
    end = _frame("end", _end_extras(app_id))

    # 开场白：新会话首轮先给（fatal/fail 也照常注入主管开场）
    opening = _opening_frames(app_id, req) if is_new else []

    # 深度思考帧（非开场白的本轮）：start 之后、正文之前
    thinking_frames: List[tuple[str, Dict[str, Any]]] = []
    if deep_thinking and scene not in ("fatal",):
        thinking_frames = [
            ("thinking", {"content": "（Mock）主管正在深度思考：分析意图…"}),
            ("thinking", {"content": "规划子任务与工具调用…"}),
        ]

    if scene == "fatal":
        yield start
        for ev, data in opening:
            await asyncio.sleep(0.18)
            yield _frame(ev, data)
        await asyncio.sleep(0.2)
        yield _frame("error", {"error": {"message": "（Mock）主管遭遇致命错误，整条流失败。"}})
        await asyncio.sleep(0.1)
        yield end
        return

    json_mode = _json_output_on(app_id)

    if scene == "direct":
        yield start
        # json_output 开启：主管正文为 JSON 字符串
        body = [
            ("message", {"content": _JSON_SUPERVISOR_1}),
            ("message", {"content": _JSON_SUPERVISOR_2}),
        ] if json_mode else [
            ("message", {"content": "这是主管直接给出的回答，"}),
            ("message", {"content": "无需派单，已为你处理完成。"}),
        ]
        for ev, data in opening + thinking_frames + body:
            await asyncio.sleep(0.18)
            yield _frame(ev, data)
        yield end
        return

    if scene == "fail":
        yield start
        for ev, data in opening + thinking_frames:
            await asyncio.sleep(0.18)
            yield _frame(ev, data)
        async for fr in _frames([
            ("agent_dispatch", {**base_owner, "agent_name": agent_name, "task": message}),
            ("error", {**base_owner, "error": {"message": "子 Agent 执行失败（Mock 错误路径）"}}),
        ]):
            yield fr
        yield end
        return

    if scene == "tool":
        step_id = _uuid()
        tool_input = {"query": message}
        yield start
        for ev, data in opening + thinking_frames:
            await asyncio.sleep(0.18)
            yield _frame(ev, data)
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
    for ev, data in opening + thinking_frames:
        await asyncio.sleep(0.18)
        yield _frame(ev, data)
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
            "output": _JSON_SUB_OUTPUT if json_mode else "这是子 Agent 的处理结果",
            "elapsed_time": 0.82,
            "token_usage": {"prompt_tokens": 120, "completion_tokens": 64, "total_tokens": 184},
        }),
    ]):
        yield fr
    yield end


# ---------------------------------------------------------------------------
# 重新生成（regenerate）：多版本管理 + 流式/非流式
# 契约对齐 app/controllers/message_interaction_controller.py 的 MULTI_AGENT 分支。
# 版本语义：同一提问下，版本号递增；新版本 is_current=true，其余兄弟 false。
# ---------------------------------------------------------------------------

# 同一提问（父 user 消息）的版本包：{"versions": [...], "current_version": int}
# key = f"{app_id}:{user_message_id}"（user_message_id 缺省用 message_id 兜底）
def _regen_key(app_id: str, user_message_id: Optional[str], message_id: str) -> str:
    return f"{app_id}:{user_message_id or message_id}"


def _get_version_pack(app_id: str, user_message_id: Optional[str], message_id: str) -> Dict[str, Any]:
    key = _regen_key(app_id, user_message_id, message_id)
    pack = _MESSAGE_VERSION_STORE.get(key)
    if pack is None:
        # 首次重新生成：构造原始 v1（来自"已存在"的原回复）
        v1 = {
            "message_id": message_id,
            "version": 1,
            "is_current": True,
            "content": "（Mock）原始回复内容。",
            "created_at": _now_ms(),
            "user_message_id": user_message_id,
        }
        pack = {"versions": [v1], "current_version": 1, "user_message_id": user_message_id}
        _MESSAGE_VERSION_STORE[key] = pack
    return pack


def _next_regen_version(pack: Dict[str, Any], conversation_id: str) -> Dict[str, Any]:
    """递增版本：旧版本 is_current=false，追加新版本为当前。"""
    for v in pack["versions"]:
        v["is_current"] = False
    new_version = (pack["versions"][-1]["version"] + 1) if pack["versions"] else 1
    nv = {
        "message_id": _uuid(),
        "version": new_version,
        "is_current": True,
        "content": f"（Mock）重新生成的第 {new_version} 版回复。",
        "created_at": _now_ms(),
        "user_message_id": pack.get("user_message_id"),
        "conversation_id": conversation_id,
    }
    pack["versions"].append(nv)
    pack["current_version"] = new_version
    return nv


async def regenerate_sse_stream(app_id: str, message_id: str, req: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """集群重新生成 SSE 契约（对齐真实实现）：
    start(带 conversation_id/message_id/user_message_id/version)
      → message×N（增量正文）→ end({}) → regenerate_end(带 message_id/version)
    """
    user_message_id = req.get("user_message_id") or req.get("parent_message_id") or _uuid()
    conversation_id = req.get("conversation_id") or _uuid()
    pack = _get_version_pack(app_id, user_message_id, message_id)
    nv = _next_regen_version(pack, conversation_id)

    yield _frame("start", {
        "conversation_id": conversation_id,
        "message_id": nv["message_id"],
        "user_message_id": user_message_id,
        "version": nv["version"],
    })
    async for fr in _frames([
        ("message", {"content": "（Mock）这是重新生成的第一部分，"}),
        ("message", {"content": "走编排器重跑，主管路由 + 子 Agent 编排，"}),
        ("message", {"content": "已生成新版本，可在版本列表中切换回看。"}),
    ]):
        yield fr
    yield _frame("end", {})
    yield _frame("regenerate_end", {
        "message_id": nv["message_id"],
        "version": nv["version"],
        "conversation_id": conversation_id,
    })


# ---------------------------------------------------------------------------
# 自包含路由：全部端点在此，不经过任何真实 controller / service。
# 必须在真实路由之前注册（路径相同，先匹配者生效）。
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/apps", tags=["Mock-Cluster"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---- 集群配置 ----
@router.get("/{app_id}/multi-agent", summary="[Mock] 读取集群配置")
async def _mock_get_multi_agent(
    app_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    return success(data=get_config(str(app_id)))


@router.put("/{app_id}/multi-agent", summary="[Mock] 保存集群配置")
async def _mock_put_multi_agent(
    app_id: uuid.UUID,
    payload: Dict[str, Any] = Body(...),
    _user: Any = Depends(get_current_user_async),
):
    return success(data=save_config(str(app_id), payload), msg="多 Agent 配置更新成功")


# ---- 调试运行 ----
@router.post("/{app_id}/draft/run", summary="[Mock] 集群调试运行（stream=true 走 SSE）")
async def _mock_draft_run(
    app_id: uuid.UUID,
    payload: Dict[str, Any] = Body(...),
    _user: Any = Depends(get_current_user_async),
):
    req = payload or {}
    if req.get("stream"):
        return StreamingResponse(
            cluster_sse_stream(str(app_id), req),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    return success(data={"response": "（Mock）这是主管给出的结果。"})


# ---- 重新生成（多版本）----
@router.post("/{app_id}/messages/{message_id}/regenerate", summary="[Mock] 重新生成集群回复")
async def _mock_regenerate(
    app_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: Dict[str, Any] = Body(default={}),
    _user: Any = Depends(get_current_user_async),
):
    req = payload or {}
    if req.get("stream"):
        return StreamingResponse(
            regenerate_sse_stream(str(app_id), str(message_id), req),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    # 非流式：返回 RegenerateResponse 形状 {message_id, message, version, conversation_id}
    user_message_id = req.get("user_message_id") or req.get("parent_message_id") or _uuid()
    conversation_id = req.get("conversation_id") or _uuid()
    pack = _get_version_pack(str(app_id), user_message_id, str(message_id))
    nv = _next_regen_version(pack, conversation_id)
    return success(data={
        "message_id": nv["message_id"],
        "message": nv["content"],
        "version": nv["version"],
        "conversation_id": conversation_id,
    })


# ---- 版本列表 ----
@router.get("/{app_id}/messages/{message_id}/versions", summary="[Mock] 获取消息的所有版本")
async def _mock_versions(
    app_id: uuid.UUID,
    message_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    # 在任一版本包中找到该 message_id 所属的包（同一提问共享）
    for pack in _MESSAGE_VERSION_STORE.values():
        if any(v["message_id"] == str(message_id) for v in pack["versions"]):
            return success(data=pack["versions"])
    # 找不到：返回该消息自身作为唯一版本
    return success(data=[{
        "message_id": str(message_id),
        "version": 1,
        "is_current": True,
        "content": "（Mock）当前回复内容。",
        "created_at": _now_ms(),
    }])


# ---- 切换版本并返回分支对话链 ----
@router.post("/{app_id}/messages/{message_id}/switch-version/{version}/branch",
             summary="[Mock] 切换版本并返回该分支完整对话链")
async def _mock_switch_version_branch(
    app_id: uuid.UUID,
    message_id: uuid.UUID,
    version: int,
    _user: Any = Depends(get_current_user_async),
):
    # 找到版本包并翻转 is_current
    target_pack = None
    for pack in _MESSAGE_VERSION_STORE.values():
        if any(v["message_id"] == str(message_id) for v in pack["versions"]):
            target_pack = pack
            break
    if target_pack:
        for v in target_pack["versions"]:
            v["is_current"] = (v["version"] == int(version))
        target_pack["current_version"] = int(version)
    # 返回对齐真实实现的分支链形状：{messages, node_executions_map, pending_intervention}
    msgs = []
    if target_pack:
        cur = next((v for v in target_pack["versions"] if v["version"] == int(version)), None)
        user_msg_id = target_pack.get("user_message_id") or _uuid()
        msgs = [
            {
                "message_id": user_msg_id,
                "role": "user",
                "content": "（Mock）用户提问",
                "version": 1,
                "is_current": True,
            },
            [
                {
                    "message_id": v["message_id"],
                    "role": "assistant",
                    "content": v["content"],
                    "version": v["version"],
                    "is_current": v["is_current"],
                } for v in target_pack["versions"]
            ],
        ]
        # 主 anchor 为目标版本 id（供前端定位）
        anchor_id = (cur or target_pack["versions"][-1])["message_id"] if target_pack["versions"] else str(message_id)
    else:
        anchor_id = str(message_id)
        msgs = []
    return success(data={
        "messages": msgs,
        "node_executions_map": {},
        "pending_intervention": {},
        "message_id": anchor_id,
    })


# ---- 收藏 / 取消收藏（幂等切换）----
@router.post("/{app_id}/messages/{message_id}/favorite", summary="[Mock] 收藏/取消收藏消息")
async def _mock_favorite(
    app_id: uuid.UUID,
    message_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    cur = _FAVORITE_STORE.get(str(message_id), False)
    new_state = not cur
    _FAVORITE_STORE[str(message_id)] = new_state
    return success(data={
        "action": "created" if new_state else "cancelled",
        "is_favorited": new_state,
    })


# ---- 点赞 / 点踩（幂等切换）----
@router.post("/{app_id}/messages/{message_id}/feedback", summary="[Mock] 提交消息反馈")
async def _mock_feedback(
    app_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: Dict[str, Any] = Body(...),
    _user: Any = Depends(get_current_user_async),
):
    fb_type = (payload or {}).get("feedback_type")
    cur = _FEEDBACK_STORE.get(str(message_id))
    if cur == fb_type:
        # 再点一次同类型 -> 取消
        _FEEDBACK_STORE[str(message_id)] = None
        return success(data={"action": "cancelled", "feedback_type": None})
    _FEEDBACK_STORE[str(message_id)] = fb_type
    return success(data={
        "action": "updated" if cur else "created",
        "feedback_type": fb_type,
    })


@router.get("/{app_id}/messages/{message_id}/feedback", summary="[Mock] 获取用户对消息的反馈")
async def _mock_get_feedback(
    app_id: uuid.UUID,
    message_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    return success(data={"feedback_type": _FEEDBACK_STORE.get(str(message_id))})


# ===========================================================================
# 标注（Annotation）：与真实 annotation_controller 同构，内存存储
# ===========================================================================

def _anno_pack(app_id: str) -> Dict[str, Any]:
    pack = _ANNOTATION_STORE.get(app_id)
    if pack is None:
        pack = {
            "setting": {
                "app_id": app_id,
                "similarity_threshold": 0.85,
                "model_config_id": None,
                "enabled": 0,
            },
            "items": [],
        }
        _ANNOTATION_STORE[app_id] = pack
    return pack


def _anno_enabled_or_raise(app_id: str) -> None:
    if _anno_pack(app_id)["setting"]["enabled"] != 1:
        raise BusinessException("请先在标注设置中启用标注功能并配置Embedding模型", BizCode.BAD_REQUEST)


def _anno_item(app_id: str, question: str, answer: str, user: Any = None) -> Dict[str, Any]:
    now = _now_ms()
    return {
        "id": _uuid(), "app_id": app_id,
        # 对齐真实 Annotation schema 字段（前端可能读取）
        "workspace_id": str(getattr(user, "current_workspace_id", _uuid())),
        "created_by": str(getattr(user, "id", _uuid())),
        "question": question, "answer": answer,
        "hit_count": 0, "created_at": now, "updated_at": now,
    }


def _anno_public(item: Dict[str, Any]) -> Dict[str, Any]:
    return dict(item)


@router.post("/{app_id}/annotations", summary="[Mock] 创建标注")
async def _mock_anno_create(
    app_id: uuid.UUID,
    payload: Dict[str, Any] = Body(...),
    _user: Any = Depends(get_current_user_async),
):
    _anno_enabled_or_raise(str(app_id))
    q, a = (payload or {}).get("question"), (payload or {}).get("answer")
    item = _anno_item(str(app_id), str(q or ""), str(a or ""), _user)
    _anno_pack(str(app_id))["items"].append(item)
    return success(data=_anno_public(item))


@router.get("/{app_id}/annotations", summary="[Mock] 标注列表（分页+搜索）")
async def _mock_anno_list(
    app_id: uuid.UUID,
    search: Optional[str] = Query(None),
    page: int = Query(1),
    pagesize: int = Query(20),
    _user: Any = Depends(get_current_user_async),
):
    items = list(_anno_pack(str(app_id))["items"])
    if search:
        s = search.strip().lower()
        items = [i for i in items if s in i["question"].lower() or s in i["answer"].lower()]
    total = len(items)
    start = (max(1, page) - 1) * max(1, pagesize)
    page_items = items[start:start + max(1, pagesize)]
    return success(data={
        "page": {"page": page, "pagesize": pagesize, "total": total,
                 "hasnext": (max(1, page) * max(1, pagesize)) < total},
        "items": [_anno_public(i) for i in page_items],
    })


@router.get("/{app_id}/annotations/settings", summary="[Mock] 标注设置")
async def _mock_anno_get_settings(
    app_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    return success(data=_anno_pack(str(app_id))["setting"])


@router.put("/{app_id}/annotations/settings", summary="[Mock] 更新标注设置")
async def _mock_anno_put_settings(
    app_id: uuid.UUID,
    payload: Dict[str, Any] = Body(...),
    _user: Any = Depends(get_current_user_async),
):
    setting = _anno_pack(str(app_id))["setting"]
    for k in ("similarity_threshold", "model_config_id", "enabled"):
        if k in (payload or {}) and payload[k] is not None:
            setting[k] = payload[k]
    return success(data=setting)


@router.get("/{app_id}/annotations/export", summary="[Mock] 批量导出标注")
async def _mock_anno_export(
    app_id: uuid.UUID,
    format: str = Query("csv"),
    _user: Any = Depends(get_current_user_async),
):
    items = [{"问题": i["question"], "答案": i["answer"]} for i in _anno_pack(str(app_id))["items"]]
    if format == "json":
        return StreamingResponse(
            io.StringIO(json.dumps(items, ensure_ascii=False, indent=2)),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=annotations.json"},
        )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["问题", "答案"])
    writer.writeheader()
    writer.writerows(items)
    return StreamingResponse(
        io.StringIO(buf.getvalue()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=annotations.csv"},
    )


@router.post("/{app_id}/annotations/import", summary="[Mock] 批量导入标注（CSV）")
async def _mock_anno_import(
    app_id: uuid.UUID,
    file: UploadFile = File(...),
    _user: Any = Depends(get_current_user_async),
):
    _anno_enabled_or_raise(str(app_id))
    content = (await file.read()).decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    n = 0
    for row in reader:
        q = (row.get("问题") or row.get("question") or "").strip()
        a = (row.get("答案") or row.get("answer") or "").strip()
        if q and a:
            _anno_pack(str(app_id))["items"].append(_anno_item(str(app_id), q, a, _user))
            n += 1
    return success(data={"count": n})


@router.delete("/{app_id}/annotations", summary="[Mock] 删除所有标注")
async def _mock_anno_delete_all(
    app_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    items = _anno_pack(str(app_id))["items"]
    n = len(items)
    items.clear()
    return success(data={"count": n})


# {annotation_id} 路由须在 /settings /export /import 之后（上面已声明），此处仅兜底详情/更新/删除/命中历史
@router.get("/{app_id}/annotations/{annotation_id}/hit-logs", summary="[Mock] 标注命中历史")
async def _mock_anno_hit_logs(
    app_id: uuid.UUID,
    annotation_id: uuid.UUID,
    page: int = Query(1),
    pagesize: int = Query(20),
    _user: Any = Depends(get_current_user_async),
):
    return success(data={
        "page": {"page": page, "pagesize": pagesize, "total": 0, "hasnext": False},
        "items": [],
    })


@router.get("/{app_id}/annotations/{annotation_id}", summary="[Mock] 标注详情")
async def _mock_anno_get(
    app_id: uuid.UUID,
    annotation_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    for i in _anno_pack(str(app_id))["items"]:
        if i["id"] == str(annotation_id):
            return success(data=_anno_public(i))
    raise BusinessException("标注不存在", BizCode.NOT_FOUND)


@router.put("/{app_id}/annotations/{annotation_id}", summary="[Mock] 更新标注")
async def _mock_anno_update(
    app_id: uuid.UUID,
    annotation_id: uuid.UUID,
    payload: Dict[str, Any] = Body(...),
    _user: Any = Depends(get_current_user_async),
):
    _anno_enabled_or_raise(str(app_id))
    for i in _anno_pack(str(app_id))["items"]:
        if i["id"] == str(annotation_id):
            if (payload or {}).get("question") is not None:
                i["question"] = str(payload["question"])
            if (payload or {}).get("answer") is not None:
                i["answer"] = str(payload["answer"])
            i["updated_at"] = _now_ms()
            return success(data=_anno_public(i))
    raise BusinessException("标注不存在", BizCode.NOT_FOUND)


@router.delete("/{app_id}/annotations/{annotation_id}", summary="[Mock] 删除标注")
async def _mock_anno_delete(
    app_id: uuid.UUID,
    annotation_id: uuid.UUID,
    _user: Any = Depends(get_current_user_async),
):
    items = _anno_pack(str(app_id))["items"]
    for idx, i in enumerate(items):
        if i["id"] == str(annotation_id):
            items.pop(idx)
            return success(msg="标注删除成功")
    raise BusinessException("标注不存在", BizCode.NOT_FOUND)


# ===========================================================================
# 注意：以下两块【不 mock】，前端直接调线上真实接口即可：
#
# 1. SSO/普通入口创建集群 = POST /api/apps（body type=multi_agent）
#    线上真实 create_app 已支持；本 mock 曾尝试覆盖该路由，实测 FastAPI 对同路径
#    同步真实路由会优先生效（穿透不可控），且 mock 接管核心建应用路由有线上风险。
# 2. 工作流 Agent 节点引用集群的选择器数据源 = GET /api/apps?type=multi_agent
#    &orchestration_mode=supervisor_loop —— 线上真实 list_apps 已有
#    orchestration_mode 过滤参数（app_controller.py:451），按发布快照筛选。
#
# 集群创建后再经本 Mock 的 PUT /apps/{app_id}/multi-agent 写配置，即完成联调闭环。
# ===========================================================================


# ===========================================================================
# 接入方式（main.py 中，在现有路由注册之前加这两行）：
#
#   # TODO(联调Mock，真实功能上线后整体移除)
#   from app.mocks import cluster_mock
#   app.include_router(cluster_mock.router, prefix="/api")
#
# 必须位于 `app.include_router(manager_router, prefix="/api")` 之前，
# 使 Mock 的同路径端点优先匹配。
# ===========================================================================
