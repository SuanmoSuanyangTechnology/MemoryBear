"""多 Agent 集群变量契约 v1（阶段一 S5）。

解决的问题：
- collaboration 链路此前只把 message / conversation_id 交给 handoffs，variables 与
  user_id / memory / storage_type / user_rag_memory_id 全部丢弃，等于"裸 LLM 群聊"；
- supervisor 链路虽然把 variables 塞进了 initial_context，但没有统一的定义来源与
  必填校验，子 Agent 各自按自己的 `variables` 定义校验，入口无法一次告知用户缺什么；
- 用户消息里的 `{{变量}}` 从未被渲染，与单 Agent 应用"配了变量就该生效"的体感不一致。

本模块把变量收敛为一条契约链路（ ClusterVariableBag ）：

    入口 variable values  ──►  定义收集（集群显式声明 > 子 Agent 定义并集）
                          ──►  必填校验 / 默认值兜底 / 未定义观测
                          ──►  渲染后的 message + 统一变量包
                                 │
                                 ├─ supervisor: 进 _analyze_task 的 initial_context → 子 Agent variables
                                 └─ collaboration: 透传给 handoffs 节点做 system_prompt / 消息渲染

渲染分两种，不要混用：
- `render_template`：完整 Jinja2 渲染，用于 system_prompt —— 与单 Agent 应用
  （ AgentRunService 里 `render_prompt_message(agent_config.system_prompt, ...)` ）同口径，
  支持 `{% if %}` 等语法；渲染失败或模板非法时退回原文，不让用户承担报错。
- `render_variables_in_text`：保守替换，只替换"值包里确实存在"的 `{{name}}`，
  用于**用户消息**这类可能包含花括号的自由文本 —— 全 Jinja 渲染会把用户输入的
  `{{乱写的东西}}` 悄悄吃成空串，这里必须保留原样。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.logging_config import get_business_logger

logger = get_business_logger()

# 变量占位符形态：`{{ name }}`。只认简单标识符，带过滤器/表达式的交给 Jinja 的全渲染。
_VARIABLE_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_JINJA_MARKERS = ("{{", "{%")

# 变量定义里"默认值"的兼容键：前端写的是 default_value，部分存量数据写 default。
_DEFAULT_KEYS = ("default_value", "default", "default_val")

SOURCE_CLUSTER = "cluster"          # 集群显式声明的对外变量
SOURCE_SUB_AGENT_UNION = "sub_agent_union"  # 由子 Agent 定义推导而来（无显式声明时的兜底）


def _as_dict(raw: Any) -> Dict[str, Any]:
    """把可能来自 JSON 列 / pydantic 对象的变量定义统一成 dict。"""
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        try:
            dumped = raw.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(raw, "__dict__"):
        return dict(vars(raw))
    return {}


def _pick_default(raw: Dict[str, Any]) -> Tuple[Any, bool]:
    """取默认值，返回 (值, 是否存在)。空串算"没有默认值"，避免把必填值覆盖成空串。"""
    for key in _DEFAULT_KEYS:
        if key in raw:
            value = raw.get(key)
            if value is None:
                return None, False
            if isinstance(value, str) and value == "":
                return None, False
            return value, True
    return None, False


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def normalize_definition(
    raw: Any,
    *,
    source: str,
    owner: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """归一化为 {name, display_name, type, required, default, options, source, owner}。"""
    data = _as_dict(raw)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    default, has_default = _pick_default(data)
    return {
        "name": name,
        "display_name": data.get("display_name") or data.get("label") or name,
        "type": data.get("type") or "string",
        "required": bool(data.get("required", False)),
        "default": default if has_default else None,
        "has_default": has_default,
        "options": data.get("options"),
        "source": source,
        "owner": owner,
    }


def _merge_definition(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """同名变量的并集合并：required 取或（任一要求必填即必填），默认值取第一个非空。"""
    target["required"] = bool(target.get("required")) or bool(incoming.get("required"))
    if not target.get("has_default") and incoming.get("has_default"):
        target["default"] = incoming.get("default")
        target["has_default"] = True
    owners = target.get("owners") or []
    if target.get("owner") and target["owner"] not in owners:
        owners.append(target["owner"])
    if incoming.get("owner") and incoming["owner"] not in owners:
        owners.append(incoming["owner"])
    target["owners"] = owners


def collect_variable_definitions(
    config: Any,
    sub_agents: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """收集集群对外变量定义。

    变量定义现统一存于 ``config.supervisor_config['variables']``（原顶层
    ``config.variables`` 列已移除）。

    优先级：
    1) 集群显式声明（`supervisor_config.variables`，沿用应用变量模型）；
    2) 否则退化为**子 Agent 定义并集**（同名合并），让存量集群也能拿到默认值与必填语义，
       不需要先做一轮数据迁移。

    Returns:
        (定义列表, 来源标记)
    """
    supervisor_config = getattr(config, "supervisor_config", None)
    declared = (
        supervisor_config.get("variables")
        if isinstance(supervisor_config, dict)
        else None
    )
    if declared:
        definitions: List[Dict[str, Any]] = []
        for raw in declared:
            normalized = normalize_definition(raw, source=SOURCE_CLUSTER)
            if normalized:
                definitions.append(normalized)
        if definitions:
            return definitions, SOURCE_CLUSTER

    merged: Dict[str, Dict[str, Any]] = {}
    for _agent_id, payload in (sub_agents or {}).items():
        agent_config = (payload or {}).get("config")
        owner = (payload or {}).get("info", {}) or {}
        owner_name = owner.get("name") or getattr(agent_config, "name", None)
        for raw in (getattr(agent_config, "variables", None) or []):
            normalized = normalize_definition(raw, source=SOURCE_SUB_AGENT_UNION, owner=owner_name)
            if not normalized:
                continue
            existing = merged.get(normalized["name"])
            if existing is None:
                merged[normalized["name"]] = normalized
            else:
                _merge_definition(existing, normalized)

    return list(merged.values()), SOURCE_SUB_AGENT_UNION


class ClusterVariableBag:
    """一轮集群执行的变量包：入口校验产物 + 统一下发值。

    - values：渲染与下发给子 Agent 的值（含集群透传的未定义项，一律保留不丢）；
    - missing_required：声明了 required 且既未传值也没有默认值的变量；
    - filled_defaults：传空/没传、由定义里的默认值兜底的变量；
    - undefined_values：传了但集群与所有子 Agent 都没定义的变量 —— 仍然下发，
      但要在日志/执行记录的 meta 里留痕，避免"配了变量没生效"无法归因。
    """

    __slots__ = (
        "values",
        "definitions",
        "source",
        "missing_required",
        "filled_defaults",
        "undefined_values",
    )

    def __init__(
        self,
        values: Dict[str, Any],
        definitions: List[Dict[str, Any]],
        source: str,
        missing_required: Optional[List[str]] = None,
        filled_defaults: Optional[List[str]] = None,
        undefined_values: Optional[List[str]] = None,
    ) -> None:
        self.values = values
        self.definitions = definitions
        self.source = source
        self.missing_required = missing_required or []
        self.filled_defaults = filled_defaults or []
        self.undefined_values = undefined_values or []

    @property
    def defined_names(self) -> List[str]:
        return [d["name"] for d in self.definitions]

    def to_observation(self) -> Dict[str, Any]:
        """观测视图：给日志 / 执行记录 meta 用（不含变量值本身，避免敏感值进日志）。"""
        return {
            "definition_source": self.source,
            "defined_count": len(self.definitions),
            "missing_required": list(self.missing_required),
            "filled_defaults": list(self.filled_defaults),
            "undefined_values": list(self.undefined_values),
        }

    def __repr__(self) -> str:  # pragma: no cover - 排障用
        return f"<ClusterVariableBag source={self.source} values={sorted(self.values)} meta={self.to_observation()}>"


def build_cluster_variable_bag(
    config: Any,
    sub_agents: Optional[Dict[str, Any]],
    values: Optional[Dict[str, Any]],
    *,
    strict_required: Optional[bool] = None,
) -> ClusterVariableBag:
    """构建本轮变量包。

    合并策略（入口侧）：
    1. 用户/调用方传入的值优先，一律保留 —— 集群是调用方，没有被子 Agent 覆盖的道理；
    2. 定义了但没传值（或传了空串）的变量，用定义里的默认值兜底；
    3. 既没传值也没默认值且 required 的，记入 missing_required；
    4. 传了但没定义的变量**不丢**，进 undefined_values 供观测。

    Args:
        strict_required: 缺失必填是否直接抛错。默认行为是：只有"集群显式声明"的必填
            缺失才抛错（那才是真正的对外契约）；由子 Agent 定义推导出来的并集不抛错 ——
            存量集群里某个子 Agent 声明了 required 并不意味着集群入口就该拦截整轮对话，
            否则会把原本能跑通（只是这个变量渲染为空）的集群直接打断。
    """
    definitions, source = collect_variable_definitions(config, sub_agents)
    incoming: Dict[str, Any] = dict(values or {})

    merged: Dict[str, Any] = dict(incoming)
    filled_defaults: List[str] = []
    missing_required: List[str] = []

    defined: Dict[str, Dict[str, Any]] = {d["name"]: d for d in definitions}
    for definition in definitions:
        name = definition["name"]
        provided = merged.get(name)
        if not _is_blank(provided):
            continue
        if definition.get("has_default"):
            merged[name] = definition["default"]
            filled_defaults.append(name)
        elif definition.get("required"):
            missing_required.append(name)

    undefined_values = [name for name in merged if name not in defined]

    if strict_required is None:
        strict_required = source == SOURCE_CLUSTER
    if strict_required and missing_required:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode

        raise BusinessException(
            f"缺少必填变量: {', '.join(missing_required)}",
            BizCode.INVALID_PARAMETER,
        )

    bag = ClusterVariableBag(
        values=merged,
        definitions=definitions,
        source=source,
        missing_required=missing_required,
        filled_defaults=filled_defaults,
        undefined_values=undefined_values,
    )

    if missing_required or undefined_values:
        logger.warning(
            "集群变量契约：存在待确认项",
            extra={
                "definition_source": source,
                "missing_required": missing_required,
                "undefined_values": undefined_values,
                "filled_defaults": filled_defaults,
            },
        )
    return bag


def has_variable_syntax(text: Any) -> bool:
    """文本里是否存在 Jinja 变量/语句标记（用于跳过无需渲染的分支，省一次解析开销）。"""
    return isinstance(text, str) and any(marker in text for marker in _JINJA_MARKERS)


def render_template(
    template: Optional[str],
    values: Optional[Dict[str, Any]],
    *,
    fallback: str = "",
) -> str:
    """完整 Jinja2 渲染（system_prompt 口径，与单 Agent 应用一致）。

    复用 `render_prompt_message`：模板里未声明的参数会被补空串，缺失变量渲染为空。
    渲染失败（语法错误/非字符串入参）时退回原文，不把模板错误抛给用户。

    Args:
        template: 模板文本
        values: 变量值
        fallback: 渲染结果为空时使用的兜底文本（默认空串）
    """
    if not template or not isinstance(template, str) or not has_variable_syntax(template):
        return template or fallback
    try:
        from app.schemas.prompt_schema import PromptMessageRole, render_prompt_message

        # render_prompt_message 会往 params 里回填缺失变量，传副本避免污染变量包
        rendered = render_prompt_message(template, PromptMessageRole.USER, dict(values or {}))
        return rendered.get_text_content() or fallback
    except Exception as exc:
        logger.warning("变量渲染失败，已退回原文", extra={"error": str(exc)})
        return template


def render_variables_in_text(text: Any, values: Optional[Dict[str, Any]]) -> str:
    """保守渲染：只替换值包里存在的 `{{name}}`，其余原样保留。

    用于用户消息 / handoff 上下文等自由文本 —— 这些文本里出现花括号通常是用户
    本意（写代码、引模板），不能被当成变量吃掉。
    """
    if not isinstance(text, str) or not text or not values or not has_variable_syntax(text):
        return text if isinstance(text, str) else (text or "")
    try:
        def _replace(match: "re.Match[str]") -> str:
            name = match.group(1)
            if name not in values:
                return match.group(0)
            value = values[name]
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float, bool)):
                return str(value)
            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(value)

        return _VARIABLE_TOKEN.sub(_replace, text)
    except Exception as exc:
        logger.warning("消息变量渲染失败，已退回原文", extra={"error": str(exc)})
        return text


def render_messages(messages: Iterable[Any], values: Optional[Dict[str, Any]]) -> List[Any]:
    """对消息列表做保守渲染（只动字符串 content 的消息对象，其它原样返回）。"""
    if not values:
        return list(messages)
    rendered: List[Any] = []
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str) and has_variable_syntax(content):
            try:
                from copy import copy

                cloned = copy(message)
                cloned.content = render_variables_in_text(content, values)
                rendered.append(cloned)
                continue
            except Exception:
                pass
        rendered.append(message)
    return rendered


def merge_sub_agent_variables(
    incoming: Optional[Dict[str, Any]],
    definitions: Optional[Iterable[Any]],
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """子 Agent 层的变量合并。

    与 `AgentRunService.prepare_variables` 的旧行为（只校验 required、原样返回入参）
    相比，这里：
    1. 集群传入的值优先保留（旧行为其实也是保留，但没有默认值兜底）；
    2. 子 Agent 定义了但集群没传 / 传空的，用定义里的默认值兜底；
    3. **不会因为子 Agent 的 required 规则抛错** —— 集群场景该由入口统一裁决，
       单个子 Agent 的必填口径不该中断整轮集群；缺失情况以返回值交还调用方观测。

    Returns:
        (合并后的值, 缺失必填变量名列表, 未定义变量名列表)
    """
    merged = dict(incoming or {})
    missing_required: List[str] = []
    defined: Dict[str, Dict[str, Any]] = {}

    for raw in (definitions or []):
        definition = normalize_definition(raw, source="sub_agent")
        if not definition:
            continue
        name = definition["name"]
        defined[name] = definition
        provided = merged.get(name)
        if not _is_blank(provided):
            continue
        if definition.get("has_default"):
            merged[name] = definition["default"]
        elif definition.get("required"):
            missing_required.append(name)

    undefined = [name for name in merged if name not in defined]
    return merged, missing_required, undefined
