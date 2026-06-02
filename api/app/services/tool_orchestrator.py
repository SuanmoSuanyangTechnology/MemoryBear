"""
工具编排器 - Prompt 驱动的 ReAct 多轮工具调用

弱模型场景下，通过 ReAct 格式的 system prompt 让模型自主决策工具调用，
多轮执行直到模型给出最终答案，将完整的工具调用过程注入 system_prompt。
"""
import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import get_business_logger

logger = get_business_logger()

# 单次调用工具的精确名称集合，超过1次调用直接提示换工具
_SINGLE_CALL_TOOLS: frozenset = frozenset({"knowledge_retrieval_tool", "long_term_memory"})


def _is_single_call_tool(name: str) -> bool:
    """判断工具是否为单次调用工具：工具对象标记优先，其次精确名称匹配"""
    return name in _SINGLE_CALL_TOOLS


# 解析模型输出的 Thought/Action/Input 三段式
_ACTION_PATTERN = re.compile(
    r"Thought[：:]\s*(.*?)\s*Action[：:]\s*(\S+)\s*Input[：:]\s*([\s\S]*?\{[\s\S]*?\})",
    re.DOTALL,
)

_REACT_SYSTEM_TEMPLATE = """\
你是具备工具调用能力的智能助手，可根据用户问题自主判断是否使用工具、选择哪个工具、分多轮完成复杂任务。

# 可用工具列表
{all_tools_info}

# 核心原则：历史信息复用优先
在决定调用工具前，**必须先检查历史对话中是否已有相关信息**：
1. 查看历史中的 Observation 字段，那里包含之前工具调用的完整结果
2. 只有当历史信息不足或与当前问题无关时，才考虑调用新工具

# 基础规则
1. 只能从上面列出的工具中选择，严禁编造不存在的工具名称。
2. 每一轮思考**只能调用一个工具**，不允许一轮同时列出多个工具。
3. 若任务需要多个步骤、依赖多个工具（如先查时间再查天气），必须分多轮依次调用。
4. **严格禁止重复调用同一工具获取相同信息**，如有结果不满足请换用其他工具或直接回答。
5. 如果用户问题不需要任何工具就能回答（或历史中已有答案），直接给出最终答案，不输出工具调用格式。
6. 工具参数缺失时，不要编造参数，正常推理是否需要继续调用。

# 输出规范
## 调用工具时，**必须严格使用以下固定三段式格式输出**，一字不差遵守排版：
Thought：你的思考过程，分析用户需求、是否需要调用工具、应该选哪个工具、缺少什么信息
Action：选中的工具名称（必须和工具列表里的名称完全一致）
Input：JSON格式工具入参，无参数则填空对象 {{}}

## 无需调用工具、信息充足可以直接回答用户时：
**直接输出纯自然语言回答即可，严禁输出 Thought、Action、Input 任何字段，不使用任何固定格式**。

# 终止条件
当已经收集到全部所需信息、无需再调用任何工具时，直接整理信息用普通自然语言回复用户，彻底停止三段式格式输出。

# 上下文说明
你能看到历史对话和上一轮工具返回的观察结果，请基于已有结果继续推理下一步动作。\
"""

# 最终回答专用提示词（给最终大模型使用，不含工具调用指令）
_FINAL_ANSWER_SYSTEM_TEMPLATE = """\
你是专业、友好、准确的智能助手。
请严格基于下方提供的【工具调用过程与结果】来回答用户问题。

规则：
1. 只使用工具返回的信息回答，不编造、不脑补、不扩展无关内容
2. 回答语言自然、通顺、礼貌，符合正常对话风格
3. 如果工具返回结果为空或失败，请如实告知用户
4. 不要输出任何 Thought/Action/Input 格式内容，只输出自然语言回答
"""


def _build_tools_info(tools: Dict[str, Any]) -> str:
    """将工具列表格式化为 prompt 中的工具描述"""
    lines = []
    for name, tool in tools.items():
        desc = getattr(tool, "description", "") or ""
        if _is_single_call_tool(name):
            desc = f"[{desc}][仅调用一次，结果不匹配时请换用其他工具]"

        param_parts = []

        # 1. 优先从 tool_instance.parameters 获取（MCP/Custom 工具）
        tool_instance = getattr(tool, "tool_instance", None)
        if tool_instance and hasattr(tool_instance, "parameters"):
            params = tool_instance.parameters
            for p in params:
                # 过滤 operation 参数（已通过工具名后缀指定）
                if p.name == "operation":
                    continue
                type_name = p.type.value if hasattr(p.type, "value") else str(p.type)
                part = f"{p.name}({type_name}"
                if p.description:
                    part += f", 说明: {p.description}"
                if p.default is not None:
                    part += f", 默认: {p.default}"
                if p.enum:
                    part += f", 可选值: {p.enum}"
                part += ")"
                param_parts.append(part)

        # 2. 回退到 args_schema（@tool 装饰器生成的工具）
        elif hasattr(tool, "args_schema") and tool.args_schema:
            try:
                from pydantic_core import PydanticUndefined
                fields = tool.args_schema.model_fields
                for k, v in fields.items():
                    type_name = v.annotation.__name__ if hasattr(v.annotation, '__name__') else str(v.annotation)
                    part = f"{k}({type_name}"
                    if v.description:
                        part += f", 说明: {v.description}"
                    if v.default is not None and v.default is not PydanticUndefined:
                        part += f", 默认: {v.default}"
                    part += ")"
                    param_parts.append(part)
            except Exception:
                pass

        if param_parts:
            lines.append(f"- {name}[{', '.join(param_parts)}]: {desc}")
        else:
            lines.append(f"- {name}: {desc}")

    return "\n".join(lines)


def _parse_action(text: str, valid_tools: set = None) -> Optional[Tuple[str, str, dict]]:
    """从模型输出中解析 Thought/Action/Input，返回 (thought, action, input_dict) 或 None"""
    m = _ACTION_PATTERN.search(text)
    if not m:
        return None
    thought = m.group(1).strip()
    action = m.group(2).strip()
    input_str = m.group(3).strip()
    # action 必须在工具列表中，否则视为终止语言（如“无”、“无需调用”）
    if valid_tools is not None and action not in valid_tools:
        return None
    try:
        brace_start = input_str.index("{")
        input_dict = json.loads(input_str[brace_start:])
    except (ValueError, json.JSONDecodeError):
        input_dict = {}
    return thought, action, input_dict


class ToolOrchestrator:
    """
    Prompt 驱动的 ReAct 多轮工具调用编排器。

    通过 ReAct 格式 system prompt 让弱模型自主决策工具调用，
    多轮执行直到模型输出最终答案，返回完整的工具调用轨迹供注入 system_prompt。

    适用场景：
    - 模型不支持 function calling（capability 中无 'function_call'）
    - 需要降低工具调用对模型能力的依赖
    """

    def __init__(self, tools: list, max_rounds: int = 5):
        """
        Args:
            tools: LangChain tool 列表
            max_rounds: 最大工具调用轮数，防止死循环
        """
        self.tools: Dict[str, Any] = {t.name: t for t in tools}
        self.max_rounds = max_rounds
        self._single_call_counts: Dict[str, int] = {}

    @classmethod
    async def create_and_run(
        cls,
        tools: list,
        system_prompt: str,
        message: str,
        history: List[Dict],
        api_key_config: Dict[str, Any],
        model_config: Any,
        effective_params: Dict[str, Any],
        processed_files: Optional[List[Dict]] = None,
        max_rounds: int = 10,
    ) -> Tuple[str, List[Dict]]:
        """
        创建编排器并执行 ReAct 循环。

        Returns:
            (updated_system_prompt, node_executions):
            - updated_system_prompt: 包含工具调用结果的 system_prompt
            - node_executions: 工具调用步骤记录列表
        """
        from app.core.models import RedBearLLM, RedBearModelConfig

        orchestrator = cls(tools, max_rounds=max_rounds)
        react_system_prompt = orchestrator.build_react_system_prompt(system_prompt)

        _react_llm = RedBearLLM(
            RedBearModelConfig(
                model_name=api_key_config["model_name"],
                provider=api_key_config.get("provider", "openai"),
                api_key=api_key_config["api_key"],
                base_url=api_key_config.get("api_base"),
                capability=api_key_config.get("capability", []),
                is_omni=api_key_config.get("is_omni", False),
                extra_params={"temperature": effective_params.get("temperature", 0.7)}
            ),
            type=model_config.type if hasattr(model_config, 'type') else model_config.model_type
        )

        async def _llm_caller(msgs):
            full_msgs = [{"role": "system", "content": react_system_prompt}] + msgs
            resp = await _react_llm.ainvoke(full_msgs)
            content = resp.content if hasattr(resp, "content") else str(resp)
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            return content.strip()

        react_message = (
            [{"type": "text", "text": message}] + processed_files
            if processed_files else message
        )

        final_answer, trajectory_context, node_executions = await orchestrator.run(
            llm_caller=_llm_caller,
            message=react_message,
            history=history
        )
        logger.info("ReAct 工具调用完成", extra={"final_answer_len": len(final_answer)})

        updated_system_prompt = (
            system_prompt + f"\n\n{_FINAL_ANSWER_SYSTEM_TEMPLATE}"
            + trajectory_context
            + f"\n\n工具调用已完成，调用结果：{final_answer}"
        )
        return updated_system_prompt, node_executions

    def build_react_system_prompt(self, original_system_prompt: str) -> str:
        """
        将原始 system_prompt 与 ReAct 工具调用指令合并。
        ReAct 指令附加在原始提示词之后。
        """
        tools_info = _build_tools_info(self.tools)
        react_block = _REACT_SYSTEM_TEMPLATE.format(all_tools_info=tools_info)
        if original_system_prompt:
            return f"{original_system_prompt}\n\n{react_block}"
        return react_block

    async def _call_tool(self, name: str, input_dict: dict) -> dict:
        """执行单个工具调用"""
        # 单次调用工具超过1次直接提示换工具
        if _is_single_call_tool(name):
            self._single_call_counts[name] = self._single_call_counts.get(name, 0) + 1
            if self._single_call_counts[name] > 1:
                return {"success": False, "output": "", "error": f"[{name}] 已调用过一次，请勿重复调用，如结果不满足需求请换用其他工具补充信息。"}
        tool = self.tools.get(name)
        if not tool:
            return {"success": False, "output": "", "error": f"工具 '{name}' 不存在"}
        try:
            # LangchainToolWrapper 使用 _arun，@tool 装饰器生成的工具使用 func
            if hasattr(tool, 'func') and callable(tool.func):
                result = await asyncio.to_thread(tool.func, **input_dict)
            else:
                # LangchainToolWrapper: 工具名可能含 operation 后缀（如 datetime_tool_now）
                # 需要从工具名中提取 operation 并注入参数
                tool_instance = getattr(tool, 'tool_instance', None)
                if tool_instance and not input_dict.get('operation') and hasattr(tool_instance, 'operation'):
                    operation = getattr(tool_instance, 'operation', '')
                    input_dict = {**input_dict, 'operation': operation}
                result = await tool._arun(**input_dict)
            logger.debug(f"工具 '{name}' 执行成功，结果长度={len(str(result))}")
            return {"success": True, "output": str(result), "error": None}
        except Exception as e:
            logger.warning(f"工具 '{name}' 执行失败: {e}")
            return {"success": False, "output": "", "error": f"[工具调用失败: {e}]"}

    async def run(self, llm_caller, message: str | list, history: List[Dict]) -> Tuple[str, str, List[Dict]]:
        """
        执行 ReAct 多轮工具调用循环。

        Args:
            llm_caller: 异步函数，签名 async (messages: list) -> str，调用底层 LLM
            message: 用户当前消息，字符串或多模态 content 列表
            history: 历史对话列表

        Returns:
            (final_answer, trajectory_context, node_executions):
            - final_answer: 模型最终自然语言回答
            - trajectory_context: 完整工具调用轨迹，用于注入 system_prompt
            - node_executions: 工具调用步骤记录列表（用于 agent_executions）
        """
        import time
        import uuid as _uuid

        # 构建初始消息列表（历史 + 当前用户消息，支持多模态 content）
        messages = list(history) + [{"role": "user", "content": message}]

        trajectory: List[str] = []  # 记录每轮 thought/action/observation
        node_executions: List[Dict] = []  # 工具调用步骤记录
        final_answer = ""
        response = ""

        for round_idx in range(self.max_rounds):
            response = await llm_caller(messages)

            parsed = _parse_action(response, valid_tools=set(self.tools.keys()))

            if parsed is None:
                # 去除可能残留的 Thought/Action/Input 块，保留其后的自然语言
                clean = re.sub(
                    r"Thought[：:][\s\S]*?Input[：:]\s*\{[^{}]*}\s*",
                    "",
                    response,
                ).strip()
                final_answer = clean or response.strip()
                logger.info(f"ReAct 第 {round_idx + 1} 轮：模型给出最终答案")
                break

            thought, action, input_dict = parsed
            logger.info(f"ReAct 第 {round_idx + 1} 轮：调用工具 '{action}'，参数={input_dict}")

            # 记录工具调用开始
            step_start = time.time()
            step_id = str(_uuid.uuid4())

            # 执行工具
            tool_result = await self._call_tool(action, input_dict)

            success: bool = bool(tool_result.get("success", True))
            output: str = str(tool_result.get("output") or "")
            error: Optional[str] = tool_result.get("error")

            observation = f"[错误: {error}]" if error else output

            # 构建步骤记录
            tool = self.tools.get(action)
            tool_meta = getattr(tool, "_tool_meta", None) if tool else None
            elapsed_ms = round((time.time() - step_start) * 1000, 2)

            node = {
                "step_id": step_id,
                "node_type": "tool",
                "node_name": action,
                "status": "completed" if success else "failed",
                "input": json.dumps(input_dict, ensure_ascii=False)[:2000],
                "output": output[:2000],
                "elapsed_time": elapsed_ms,
                "error": error,
                "meta": tool_meta if tool_meta else None,
            }
            # 提取知识库来源
            if tool and hasattr(tool, "_last_sources") and tool._last_sources:
                if not node["meta"]:
                    node["meta"] = {}
                node["meta"]["sources"] = tool._last_sources
                tool._last_sources = []
            node_executions.append(node)

            # 记录本轮轨迹
            trajectory.append(
                f"Thought：{thought}\n"
                f"Action：{action}\n"
                f"Input：{json.dumps(input_dict, ensure_ascii=False)}\n"
                f"Observation：{observation}"
            )

            # 将本轮 ReAct 轨迹合并为一条 assistant 消息，供下一轮推理
            messages.append({"role": "assistant", "content": f"{response}\nObservation：{observation}"})

        else:
            # 达到最大轮数，强制用最后一次模型输出作为答案
            logger.warning(f"ReAct 达到最大轮数 {self.max_rounds}，强制终止")
            final_answer = response.strip() if response else "已达到最大思考轮次，无法继续"

        trajectory_context = self.build_trajectory_context(trajectory)
        return final_answer, trajectory_context, node_executions

    @staticmethod
    def build_trajectory_context(trajectory: List[str]) -> str:
        """将工具调用轨迹格式化为注入 system_prompt 的上下文段落"""
        if not trajectory:
            return ""
        parts = ["\n\n---\n以下是工具调用过程与结果，请基于这些信息给出最终回答："]
        parts.extend(trajectory)
        parts.append("---")
        return "\n\n".join(parts)
