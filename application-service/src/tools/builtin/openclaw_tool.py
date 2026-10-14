"""OpenClaw remote Agent builtin tool."""
from typing import List, Dict, Any

from .base import BuiltinTool
from ...schemas.tool_schema import ToolParameter, ParameterType


class OpenClawTool(BuiltinTool):
    """OpenClaw remote Agent tool - supports text and image multimodal input."""

    def __init__(self, tool_id: str, config: Dict[str, Any]):
        super().__init__(tool_id, config)
        params = self.parameters_config

        # User config items (filled in via the frontend form)
        self._server_url = params.get("server_url", "")
        self._api_key = params.get("api_key", "")
        self._agent_id = params.get("agent_id", "main")

        # Internal defaults
        self._model = "openclaw"
        self._session_strategy = "by_user"
        self._timeout = 120

        # Runtime context (injected via set_runtime_context)
        self._user_id = "anonymous"
        self._conversation_id = None
        self._uploaded_files = []

    @property
    def name(self) -> str:
        return "openclaw_tool"

    @property
    def description(self) -> str:
        return (
            "OpenClaw 远程 Agent：将任务委托给远程 OpenClaw Agent。"
            "具备 3D 模型生成与打印控制、设备管理、文件处理、浏览器自动化、"
            "Shell 命令执行、网络搜索等能力。支持文本和图片多模态交互。"
        )

    def get_required_config_parameters(self) -> List[str]:
        return ["server_url", "api_key"]

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="任务类型",
                required=True,
                enum= ["print_task", "device_query", "image_understand", "general"]
            ),
            ToolParameter(
                name="message",
                type=ParameterType.STRING,
                description="发送给 OpenClaw Agent 的文本请求内容",
                required=True
            ),
            ToolParameter(
                name="image_url",
                type=ParameterType.STRING,
                description="可选，附带的图片 URL 或 base64 data URI（OpenClaw 支持图片输入）",
                required=False
            )
        ]
