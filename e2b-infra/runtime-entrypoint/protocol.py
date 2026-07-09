"""
Sandbox ↔ API Communication Protocol

定义 sandbox 内执行引擎与主 API 之间的通信协议。

输出协议（stdout JSON Lines）：
    sandbox 通过 stdout 输出 JSON Lines 格式的事件流。
    每行一个 JSON 对象，格式为：
    {"event": "<event_type>", "data": {...}}

事件类型：
    - execution_start: 执行开始
    - execution_end: 执行完成
    - execution_error: 执行出错
    - agent_chunk: Agent 流式输出片段
    - agent_tool_start: 工具开始执行
    - agent_tool_end: 工具执行完成
    - agent_thinking: 思考/推理内容
    - workflow_start: 工作流开始
    - workflow_end: 工作流完成
    - node_start: 节点开始执行
    - node_end: 节点执行完成
    - node_chunk: 节点流式输出
"""
import json
import sys
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    """Sandbox output event types"""
    # Lifecycle
    EXECUTION_START = "execution_start"
    EXECUTION_END = "execution_end"
    EXECUTION_ERROR = "execution_error"

    # Agent events
    AGENT_CHUNK = "agent_chunk"
    AGENT_TOOL_START = "agent_tool_start"
    AGENT_TOOL_END = "agent_tool_end"
    AGENT_THINKING = "agent_thinking"
    AGENT_MESSAGE = "agent_message"

    # Workflow events
    WORKFLOW_START = "workflow_start"
    WORKFLOW_END = "workflow_end"
    NODE_START = "node_start"
    NODE_END = "node_end"
    NODE_CHUNK = "node_chunk"


@dataclass
class SandboxEvent:
    """A single event emitted from the sandbox runtime"""
    event: str
    data: dict
    timestamp: float = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_json(self) -> str:
        return json.dumps(
            {"event": self.event, "data": self.data, "timestamp": self.timestamp},
            ensure_ascii=False,
            default=str,
        )


class EventEmitter:
    """Emits events to stdout in JSON Lines format

    主 API 通过读取 sandbox 进程的 stdout 来接收这些事件。
    """

    def emit(self, event_type: str | EventType, data: dict | None = None):
        """Emit a single event to stdout"""
        event = SandboxEvent(
            event=event_type.value if isinstance(event_type, EventType) else event_type,
            data=data or {},
        )
        line = event.to_json()
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def emit_start(self, execution_type: str, execution_id: str, **kwargs):
        """Emit execution start event"""
        self.emit(EventType.EXECUTION_START, {
            "execution_type": execution_type,
            "execution_id": execution_id,
            **kwargs,
        })

    def emit_end(self, result: dict, **kwargs):
        """Emit execution end event"""
        self.emit(EventType.EXECUTION_END, {
            "result": result,
            **kwargs,
        })

    def emit_error(self, error: str, error_type: str = "RuntimeError", **kwargs):
        """Emit execution error event"""
        self.emit(EventType.EXECUTION_ERROR, {
            "error": error,
            "error_type": error_type,
            **kwargs,
        })

    def emit_agent_chunk(self, content: str, **kwargs):
        """Emit agent streaming chunk"""
        self.emit(EventType.AGENT_CHUNK, {"content": content, **kwargs})

    def emit_agent_tool_start(self, tool_name: str, tool_input: Any, **kwargs):
        """Emit tool execution start"""
        self.emit(EventType.AGENT_TOOL_START, {
            "tool_name": tool_name,
            "tool_input": tool_input,
            **kwargs,
        })

    def emit_agent_tool_end(self, tool_name: str, tool_output: Any, **kwargs):
        """Emit tool execution end"""
        self.emit(EventType.AGENT_TOOL_END, {
            "tool_name": tool_name,
            "tool_output": tool_output,
            **kwargs,
        })

    def emit_node_start(self, node_id: str, node_type: str, **kwargs):
        """Emit workflow node start"""
        self.emit(EventType.NODE_START, {
            "node_id": node_id,
            "node_type": node_type,
            **kwargs,
        })

    def emit_node_end(self, node_id: str, output: Any, **kwargs):
        """Emit workflow node end"""
        self.emit(EventType.NODE_END, {
            "node_id": node_id,
            "output": output,
            **kwargs,
        })

    def emit_node_chunk(self, node_id: str, content: str, **kwargs):
        """Emit workflow node streaming chunk"""
        self.emit(EventType.NODE_CHUNK, {
            "node_id": node_id,
            "content": content,
            **kwargs,
        })


# Global emitter instance
emitter = EventEmitter()
