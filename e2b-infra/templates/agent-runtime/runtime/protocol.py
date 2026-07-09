"""Sandbox event protocol - outputs JSON Lines to stdout"""
import json
import sys
import time
from enum import Enum


class EventType(str, Enum):
    EXECUTION_START = "execution_start"
    EXECUTION_END = "execution_end"
    EXECUTION_ERROR = "execution_error"
    AGENT_CHUNK = "agent_chunk"
    AGENT_TOOL_START = "agent_tool_start"
    AGENT_TOOL_END = "agent_tool_end"
    AGENT_THINKING = "agent_thinking"
    WORKFLOW_START = "workflow_start"
    WORKFLOW_END = "workflow_end"
    NODE_START = "node_start"
    NODE_END = "node_end"
    NODE_CHUNK = "node_chunk"


class EventEmitter:
    def emit(self, event_type, data=None):
        line = json.dumps(
            {"event": event_type.value if isinstance(event_type, EventType) else event_type,
             "data": data or {}, "timestamp": time.time()},
            ensure_ascii=False, default=str,
        )
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def emit_start(self, execution_type, execution_id, **kw):
        self.emit(EventType.EXECUTION_START, {"execution_type": execution_type, "execution_id": execution_id, **kw})

    def emit_end(self, result, **kw):
        self.emit(EventType.EXECUTION_END, {"result": result, **kw})

    def emit_error(self, error, error_type="RuntimeError", **kw):
        self.emit(EventType.EXECUTION_ERROR, {"error": error, "error_type": error_type, **kw})

    def emit_agent_chunk(self, content, **kw):
        self.emit(EventType.AGENT_CHUNK, {"content": content, **kw})

    def emit_agent_tool_start(self, tool_name, tool_input, **kw):
        self.emit(EventType.AGENT_TOOL_START, {"tool_name": tool_name, "tool_input": tool_input, **kw})

    def emit_agent_tool_end(self, tool_name, tool_output, **kw):
        self.emit(EventType.AGENT_TOOL_END, {"tool_name": tool_name, "tool_output": tool_output, **kw})

    def emit_node_start(self, node_id, node_type, **kw):
        self.emit(EventType.NODE_START, {"node_id": node_id, "node_type": node_type, **kw})

    def emit_node_end(self, node_id, output, **kw):
        self.emit(EventType.NODE_END, {"node_id": node_id, "output": output, **kw})

    def emit_node_chunk(self, node_id, content, **kw):
        self.emit(EventType.NODE_CHUNK, {"node_id": node_id, "content": content, **kw})


emitter = EventEmitter()
