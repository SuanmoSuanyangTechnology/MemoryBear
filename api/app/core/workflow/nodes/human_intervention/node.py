import datetime
import logging
import re
from collections import defaultdict
from typing import Any

from langgraph.types import interrupt

from app.core.utils.datetime_utils import parse_iso_to_utc_naive, to_iso_z, utcnow
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.human_intervention.config import HumanInterventionNodeConfig
from app.core.workflow.variable.base_variable import VariableType

logger = logging.getLogger(__name__)


class TimeoutEntry:
    """Tracks a pending timeout for a human-intervention workflow.

    One entry per visible intervention node. Parallel intervention nodes can
    become visible at different times, so their timeout clocks must not share a
    single execution-level timer.
    """

    __slots__ = ("execution_id", "node_id", "timeout_at")

    def __init__(
        self,
        execution_id: str,
        node_id: str,
        timeout_at: datetime.datetime,
    ):
        self.execution_id = execution_id
        self.node_id = node_id
        self.timeout_at = timeout_at


class InterventionRegistry:
    """Per-execution registry for intervention data.

    Saves intervention data BEFORE interrupt() is called, so that even if
    LangGraph cancels a parallel task (losing the interrupt), we can still
    recover the intervention data from this registry.

    See: LangGraph 1.0.10 _panic_or_proceed cancels inflight tasks on GraphInterrupt,
    causing parallel interrupt() calls to lose data.
    """

    _data: dict[str, dict[str, dict]] = defaultdict(dict)
    _timeout_entries: dict[tuple[str, str], TimeoutEntry] = {}

    @classmethod
    def register(cls, execution_id: str, node_id: str, intervention_data: dict):
        cls._data[execution_id][node_id] = dict(intervention_data)
        logger.debug(f"[InterventionRegistry] registered: execution={execution_id}, node={node_id}")

    @classmethod
    def register_timeout(cls, entry: TimeoutEntry):
        cls._timeout_entries[(entry.execution_id, entry.node_id)] = entry
        logger.debug(
            f"[InterventionRegistry] registered timeout: execution={entry.execution_id}, "
            f"node={entry.node_id}, timeout_at={to_iso_z(entry.timeout_at)}"
        )

    @classmethod
    def remove_timeout(cls, execution_id: str, node_id: str | None = None):
        if node_id is not None:
            cls._timeout_entries.pop((execution_id, node_id), None)
            return
        for key in list(cls._timeout_entries.keys()):
            if key[0] == execution_id:
                cls._timeout_entries.pop(key, None)

    @classmethod
    def get_expired(cls) -> list[TimeoutEntry]:
        now = utcnow()
        return [e for e in cls._timeout_entries.values() if e.timeout_at <= now]

    @classmethod
    def get_all(cls, execution_id: str) -> dict[str, dict]:
        return dict(cls._data.get(execution_id, {}))

    @classmethod
    def cleanup(cls, execution_id: str):
        cls._data.pop(execution_id, None)
        cls.remove_timeout(execution_id)

    @classmethod
    def cleanup_node(cls, execution_id: str, node_id: str):
        """Remove a single node's intervention data from the registry.

        Call this after a node is successfully resumed (via Command(resume=...)
        or Command(goto=...)) so the recovery logic in resume_workflow_stream()
        does not re-add the completed node as a "lost" interrupt.
        """
        exec_data = cls._data.get(execution_id)
        cls.remove_timeout(execution_id, node_id)
        if exec_data:
            exec_data.pop(node_id, None)
            remaining = len(exec_data)
            if not exec_data:
                cls._data.pop(execution_id, None)
            logger.debug(
                f"[InterventionRegistry] cleaned up node: execution={execution_id}, node={node_id}, "
                f"remaining={remaining}"
            )


class HumanInterventionNode(BaseNode):
    """
    人工介入节点

    暂停工作流，向用户展示表单和填写字段，等待用户操作后恢复执行。
    利用 LangGraph 的 interrupt() / Command(resume=...) 机制实现暂停/恢复。

    输出变量（无表单字段时）:
      - __action_id:       用户触发的操作 ID（超时时为 "__timeout__"）
      - __rendered_content: 渲染后的表单内容

    输出变量（有表单字段时，额外输出每个字段的值）:
      - <field_id>:        用户填写的对应字段值（超时时不输出）

    内部路由字段（不输出到节点结果中，仅供条件分支路由）:
      - __route:           CASE_N 路由标识（超时时为 "TIMEOUT"）
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: HumanInterventionNodeConfig | None = None
        self._waiting_since: str | None = None
        self._responded_at: str | None = None
        self._action_id: str | None = None
        self._form_data: dict | None = None
        self._form_fields: list[dict] | None = None

    def _output_types(self) -> dict[str, VariableType]:
        result = {
            "__action_id": VariableType.STRING,
            "__rendered_content": VariableType.STRING,
            "__route": VariableType.STRING,
        }
        if self._form_fields:
            for f in self._form_fields:
                # All fields are STRING type now (simplified to editable text / display variable)
                result[f["id"]] = VariableType.STRING
        return result

    def _extract_extra_fields(self, business_result: Any) -> dict:
        process = {}
        if self._waiting_since:
            process["waiting_since"] = self._waiting_since
        if self._responded_at:
            process["responded_at"] = self._responded_at
        if self._waiting_since and self._responded_at:
            try:
                t1 = parse_iso_to_utc_naive(self._waiting_since)
                t2 = parse_iso_to_utc_naive(self._responded_at)
                process["waited_seconds"] = (t2 - t1).total_seconds()
            except (ValueError, TypeError):
                pass
        if self._action_id:
            process["action_id"] = self._action_id
        # 预览模式元数据：将预览特有的信息提取到 process 中
        if isinstance(business_result, dict) and business_result.get("__preview"):
            process["preview"] = True
            process["preview_actions"] = business_result.get("__preview_actions", [])
            process["preview_form_fields"] = business_result.get("__preview_form_fields", [])
            process["preview_timeout_at"] = business_result.get("__preview_timeout_at")
            process["preview_delivery_method"] = business_result.get("__preview_delivery_method")
        return {"process": process} if process else {}

    def _calculate_timeout(self, timeout_config) -> datetime.datetime | None:
        if not timeout_config:
            return None
        now = utcnow()
        unit = timeout_config.unit
        value = timeout_config.value
        if unit == "days":
            delta = datetime.timedelta(days=value)
        elif unit == "hours":
            delta = datetime.timedelta(hours=value)
        elif unit == "minutes":
            delta = datetime.timedelta(minutes=value)
        else:
            delta = datetime.timedelta(seconds=value)
        return now + delta

    def _inject_route_variable(self, result: dict[str, Any], variable_pool: VariablePool):
        """Manually inject __route into variable_pool for conditional routing.

        Ensures __route is available in the variable pool at the correct time
        for the routing condition (node['<id>']['__route'] == 'CASE1') to find
        it during execution. Also persists __route in node_outputs via
        _extract_output so resume_workflow_stream can recover it from the
        graph state checkpoint.
        """
        route_value = result.get("__route")
        if route_value is not None:
            from app.core.workflow.variable.base_variable import VariableType
            from app.core.workflow.variable.variable_objects import create_variable_instance
            from app.core.workflow.engine.variable_pool import VariableStruct
            inst = create_variable_instance(VariableType.STRING, str(route_value))
            variable_pool.variables.setdefault(self.node_id, {})["__route"] = VariableStruct(
                type=VariableType.STRING, instance=inst, mut=self.variable_change_able
            )

    def _extract_output(self, business_result: Any) -> Any:
        """Keep __route in stored output for resume pre-population.

        __route must persist in node_outputs so that resume_workflow_stream
        can recover it from the graph state checkpoint and properly activate
        the downstream End node's StreamOutputCoordinator (control_resolved).

        This mirrors how the LLM node keeps branch_signal in its output.

        In preview mode, strips __preview* keys so they only appear in process.
        """
        if isinstance(business_result, dict):
            output = dict(business_result)
            # 去除预览元数据键（仅在 process 中保留）
            preview_keys = {
                "__preview", "__preview_actions", "__preview_form_fields",
                "__preview_timeout_at", "__preview_delivery_method",
            }
            for key in preview_keys:
                output.pop(key, None)
            return output
        return business_result

    def _build_result(self, rendered_content: str, action_id: str, case_index: int | None, form_data: dict | None = None, variable_pool: VariablePool | None = None) -> dict[str, Any]:
        result = {
            "__action_id": action_id,
            "__rendered_content": rendered_content,
            "__route": "TIMEOUT" if action_id == "__timeout__" else (f"CASE{case_index + 1}" if case_index is not None else "__pending__"),
        }
        if self._form_fields:
            for f in self._form_fields:
                field_id = f["id"]
                # 区分类型：通过 variable_ref 是否存在来判断
                if f.get("variable_ref") is not None:
                    var_ref = f.get("variable_ref")
                    val = variable_pool.get_value(var_ref, default=None, strict=False) if variable_pool and var_ref else None
                else:
                    val = form_data.get(field_id) if form_data else None
                    if val is None:
                        val = f.get("default_value")
                if val is None:
                    val = ""
                elif not isinstance(val, str):
                    val = str(val)
                result[field_id] = val

        # 将 {{form_field:id}} 占位符替换为实际字段值，
        # 这样 End 节点引用 __rendered_content 时能看到完整内容而非原始占位符
        final_content = result["__rendered_content"]
        for f in (self._form_fields or []):
            field_id = f["id"]
            field_value = result.get(field_id)
            placeholder = f"{{{{form_field:{field_id}}}}}"
            replacement = str(field_value) if field_value is not None else ""
            final_content = final_content.replace(placeholder, replacement)
        result["__rendered_content"] = final_content

        return result

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        self.typed_config = HumanInterventionNodeConfig(**self.config)

        rendered_content = self._render_template(
            self.typed_config.content, variable_pool, strict=False
        )

        timeout_at = self._calculate_timeout(self.typed_config.timeout)

        actions = [
            {"id": a.id, "label": a.label, "variant": a.variant}
            for a in self.typed_config.actions
        ]

        # Parse {{form_field:id}} placeholders from rendered_content
        # These are inserted by ctrl+/ in the editor and define inline form fields
        # Note: type/mode/default_value/variable_ref are looked up from config by id
        content_form_field_ids: set[str] = set()
        for match in re.finditer(r'\{\{form_field:([^}]+)\}\}', rendered_content):
            field_id = match.group(1).strip()
            if field_id:
                content_form_field_ids.add(field_id)

        # Build form_fields from config; the type is implicit by which key is present.
        # Only emit the key that is actually set (drop the None counterpart) so the
        # frontend can distinguish text vs variable fields via key presence, not by
        # checking for `null` vs `undefined`.
        def _serialize_form_field(f) -> dict:
            data: dict = {"id": f.id}
            if f.variable_ref is not None:
                data["variable_ref"] = f.variable_ref
            else:
                data["default_value"] = f.default_value
            return data

        config_form_fields = [_serialize_form_field(f) for f in self.typed_config.form_fields]
        # Deduplicate by id: config fields take priority
        seen_ids = {f["id"] for f in config_form_fields}
        # Append content-only placeholders (no config), default to text type with empty default
        content_only = [
            {"id": fid, "default_value": None, "variable_ref": None}
            for fid in content_form_field_ids if fid not in seen_ids
        ]
        form_fields = config_form_fields + content_only

        # Store form_fields so _output_types can use them
        self._form_fields = form_fields if form_fields else None

        delivery_method = self.typed_config.delivery_method

        # ---- 单节点试运行（两阶段交互式预览） ----
        # 当 state 中有 __single_node_run 标志时（由 WorkflowService 注入），
        # 跳过 interrupt() 调用，支持两阶段交互：
        #   Phase 1（无 action_id）：返回表单结构，供前端展示交互式表单
        #   Phase 2（有 action_id）：用用户选择的操作和表单数据构建实际结果
        if state.get("__single_node_run"):
            preview_actions = [{"id": a.id, "label": a.label, "variant": a.variant} for a in self.typed_config.actions]
            action_id = state.get("__single_node_action_id")
            form_data = state.get("__single_node_form_data") or {}

            if action_id:
                # Phase 2：用户已选择操作，构建实际输出结果
                logger.info(f"Node {self.node_id}: single node run action '{action_id}'")
                action_ids = [a["id"] for a in preview_actions]
                if action_id in action_ids:
                    case_index = action_ids.index(action_id)
                else:
                    case_index = len(preview_actions)

                self._action_id = action_id
                self._form_data = form_data
                self._waiting_since = to_iso_z(utcnow())
                self._responded_at = to_iso_z(utcnow())

                result = self._build_result(rendered_content, action_id, case_index, form_data, variable_pool)
                self._inject_route_variable(result, variable_pool)
                return result

            # Phase 1：返回表单结构供前端展示
            logger.info(f"Node {self.node_id}: single node run preview, returning form structure")

            result = {
                "__action_id": "__preview__",
                "__rendered_content": rendered_content,
                "__route": "__preview__",
            }
            if self._form_fields:
                for f in self._form_fields:
                    field_id = f["id"]
                    if f.get("variable_ref") is not None:
                        var_ref = f.get("variable_ref")
                        result[field_id] = (
                            variable_pool.get_value(var_ref, default=None, strict=False)
                            if variable_pool and var_ref else None
                        )
                    else:
                        result[field_id] = f.get("default_value")

            result["__preview"] = True
            result["__preview_actions"] = preview_actions
            result["__preview_form_fields"] = form_fields
            result["__preview_timeout_at"] = to_iso_z(timeout_at) if timeout_at else None
            result["__preview_delivery_method"] = {
                "webapp": {"enabled": delivery_method.webapp.enabled},
                "email": {"enabled": delivery_method.email.enabled},
            }

            self._action_id = "__preview__"
            self._waiting_since = to_iso_z(utcnow())
            self._inject_route_variable(result, variable_pool)
            return result

        intervention_data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "execution_id": state["execution_id"],
            "delivery_method": {
                "webapp": {
                    "enabled": delivery_method.webapp.enabled,
                },
                "email": {
                    "enabled": delivery_method.email.enabled,
                },
            },
            "rendered_content": rendered_content,
            "form_fields": form_fields,
            "actions": actions,
            "timeout_at": to_iso_z(timeout_at) if timeout_at else None,
        }

        # Register intervention data BEFORE interrupt() so it survives task cancellation.
        # See: LangGraph 1.0.10 _panic_or_proceed cancels inflight parallel tasks on
        # GraphInterrupt, causing the 3rd (or later) parallel interrupt to be lost.
        InterventionRegistry.register(state["execution_id"], self.node_id, intervention_data)

        pre_action_id = None
        pre_form_data = None
        node_output = (state.get("node_outputs", {}).get(self.node_id) or {})
        if isinstance(node_output, dict):
            pre_action_id = node_output.get("__pre_action_id")
            pre_form_data = node_output.get("__form_data")

        # If __pre_action_id is "__pending__", this node was marked as pending during
        # a previous Command(resume=...) call. Another parallel node was resolved;
        # this one should return immediately without calling interrupt() again.
        if pre_action_id == "__pending__":
            logger.info(f"Node {self.node_id}: __pending__ marker found, returning placeholder")
            self._action_id = "__pending__"
            result = self._build_result(rendered_content, "__pending__", None, None, variable_pool)
            self._inject_route_variable(result, variable_pool)
            return result

        if pre_action_id:
            logger.info(
                f"Node {self.node_id}: re-executing with pre-provided action '{pre_action_id}'"
            )
            self._action_id = pre_action_id
            self._form_data = pre_form_data
            self._waiting_since = to_iso_z(utcnow())
            self._responded_at = to_iso_z(utcnow())
            action_ids = [a["id"] for a in actions]
            if self._action_id in action_ids:
                case_index = action_ids.index(self._action_id)
            else:
                case_index = len(actions)
            logger.info(
                f"Node {self.node_id}: re-executed action '{self._action_id}', "
                f"routing to CASE{case_index + 1}"
            )
            result = self._build_result(rendered_content, self._action_id, case_index, pre_form_data, variable_pool)
            self._inject_route_variable(result, variable_pool)
            return result

        self._waiting_since = to_iso_z(utcnow())

        user_response = interrupt(intervention_data)

        if isinstance(user_response, dict) and user_response.get("__pending__"):
            logger.info(f"Node {self.node_id}: received __pending__, completing with placeholder")
            self._action_id = "__pending__"
            result = self._build_result(rendered_content, "__pending__", None, None, variable_pool)
            self._inject_route_variable(result, variable_pool)
            return result

        self._responded_at = to_iso_z(utcnow())

        if not user_response or not isinstance(user_response, dict):
            self._action_id = "__timeout__"
            self._form_data = None
            case_index = len(actions)
        elif user_response.get("__timeout__"):
            self._action_id = "__timeout__"
            self._form_data = None
            case_index = len(actions)
        else:
            self._action_id = user_response.get("action_id", "")
            self._form_data = user_response.get("form_data")
            action_ids = [a["id"] for a in actions]
            if self._action_id in action_ids:
                case_index = action_ids.index(self._action_id)
            else:
                case_index = len(actions)

        logger.info(
            f"Node {self.node_id}: human responded with action '{self._action_id}', "
            f"routing to {'TIMEOUT' if self._action_id == '__timeout__' else f'CASE{case_index + 1}'}"
        )

        result = self._build_result(rendered_content, self._action_id, case_index, self._form_data, variable_pool)
        self._inject_route_variable(result, variable_pool)
        return result
