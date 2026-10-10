"""集群 supervisor_loop 对话能力（features）的共享纯函数。

集群有多个入口（分享 / API Key / 试运行；流式与非流式），对话能力的判定逻辑集中在这里，
各入口只调用本模块，不各写一份。

向后兼容约定：存量集群没有 `supervisor_config.features`、请求也不带新参数，
本模块所有函数在这种输入下必须返回"关闭 / 空"，调用方据此保持旧行为。
"""
from typing import Any, Dict, Optional, Tuple

EXECUTION_MODE_IN_PROCESS = "in_process"
EXECUTION_MODE_SANDBOX = "sandbox"


def _to_dict(value: Any) -> Dict[str, Any]:
    """pydantic 模型 / dict / 其它 → dict（其它一律空 dict）。"""
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump()
        except Exception:  # noqa: BLE001 - 脏值不阻断运行
            return {}
    return value if isinstance(value, dict) else {}


def get_features(supervisor_config: Any) -> Dict[str, Any]:
    """从 supervisor_config 取 features（缺省 / 脏值 → 空 dict = 全部关闭）。"""
    return _to_dict(_to_dict(supervisor_config).get("features"))


def get_features_from_config(config: Any) -> Dict[str, Any]:
    """从集群配置对象（ORM / 发布快照代理）取 features。"""
    return get_features(getattr(config, "supervisor_config", None))


def is_supervisor_web_search_on(features: Dict[str, Any], request_web_search: bool) -> bool:
    """主管联网：features.web_search.enabled 与请求 web_search 同时为真才生效。

    子 Agent 的联网不走这里（仍由请求参数透传，不受集群 features 管）。
    """
    web_cfg = _to_dict((features or {}).get("web_search"))
    return bool(web_cfg.get("enabled")) and bool(request_web_search)


def is_deep_thinking_on(model_parameters: Any, request_thinking: bool) -> bool:
    """深度思考：集群 model_parameters.deep_thinking 与请求 thinking 同时为真（与 Agent 入口口径一致）。"""
    mp = _to_dict(model_parameters)
    if not mp and model_parameters is not None and not isinstance(model_parameters, dict):
        mp = {"deep_thinking": getattr(model_parameters, "deep_thinking", False)}
    return bool(mp.get("deep_thinking")) and bool(request_thinking)


def resolve_opening(
    features: Dict[str, Any],
    variables: Optional[Dict[str, Any]],
    is_new_conversation: bool,
) -> Tuple[Optional[str], list]:
    """新会话开场白（含 {{var}} 简单替换）。未开启 / 非新会话 → (None, [])。

    与 Agent 应用同源：复用 AgentRunService._get_opening_statement。
    """
    if not is_new_conversation or not features:
        return None, []
    opening_cfg = _to_dict(features.get("opening_statement"))
    if not (opening_cfg.get("enabled") and opening_cfg.get("statement")):
        return None, []
    from app.services.draft_run_service import AgentRunService

    # _get_opening_statement 直接读取 opening["suggested_questions"]，缺键会 KeyError，这里先补齐
    safe_features = dict(features)
    safe_features["opening_statement"] = {
        **opening_cfg,
        "suggested_questions": opening_cfg.get("suggested_questions") or [],
    }
    statement, questions = AgentRunService._get_opening_statement(safe_features, True, variables)
    return statement, list(questions or [])


def resolve_execution_mode(settings_e2b_enabled: bool) -> str:
    """入口执行模式：与 Agent 同规则（E2B_ENABLED → sandbox，否则 in_process）。"""
    return EXECUTION_MODE_SANDBOX if settings_e2b_enabled else EXECUTION_MODE_IN_PROCESS


def effective_execution_mode(requested: Optional[str]) -> Tuple[str, bool]:
    """集群主管当前只有 in_process 实现。返回 (实际模式, 是否发生了回退)。

    sandbox 的集群实现留待后续；这里保证不会静默：调用方据回退标志打 warning 并写入 end 事件。
    """
    if requested == EXECUTION_MODE_SANDBOX:
        return EXECUTION_MODE_IN_PROCESS, True
    return EXECUTION_MODE_IN_PROCESS, False
