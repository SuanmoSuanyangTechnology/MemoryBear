"""OpenClaw remote Agent builtin tool."""
import base64
import logging
import time
from io import BytesIO
from typing import Any, Dict, List, Optional

import aiohttp

from .base import BuiltinTool
from ...schemas.tool_schema import ToolParameter, ParameterType, ToolResult


logger = logging.getLogger(__name__)


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

    # ---------- Runtime context injection ----------
    def set_runtime_context(
        self,
        user_id: str = "anonymous",
        conversation_id: Optional[str] = None,
        uploaded_files: Optional[list] = None
    ):
        """Inject the runtime context (called by the chat service)."""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._uploaded_files = uploaded_files or []

    # ---------- Connection test ----------
    async def test_connection(self) -> Dict[str, Any]:
        """Test the OpenClaw Gateway connection."""
        if not self._server_url:
            return {"success": False, "message": "未配置 server_url"}
        if not self._api_key:
            return {"success": False, "message": "未配置 api_key"}

        url = f"{self._server_url.rstrip('/')}/v1/responses"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "x-openclaw-agent-id": self._agent_id
        }
        body = {
            "model": self._model,
            "user": "connection-test",
            "input": "hi",
            "stream": False
        }
        try:
            timeout_cfg = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    if resp.status < 400:
                        return {"success": True, "message": "OpenClaw 连接成功"}
                    error_text = await resp.text()
                    return {
                        "success": False,
                        "message": f"OpenClaw HTTP {resp.status}: {error_text[:200]}"
                    }
        except Exception as e:
            return {"success": False, "message": f"OpenClaw 连接失败: {str(e)}"}

    # ---------- Execution ----------
    async def execute(self, **kwargs) -> ToolResult:
        """Execute an OpenClaw call."""
        start_time = time.time()
        try:
            message = kwargs.get("message", "")
            if not message:
                return ToolResult.error_result(
                    error="message 参数不能为空",
                    error_code="OPENCLAW_INVALID_INPUT",
                    execution_time=time.time() - start_time
                )

            # Extract the image: prefer user-uploaded files, fall back to the
            # image_url passed by the LLM
            image_url = self._extract_image_from_uploads()
            if not image_url:
                image_url = kwargs.get("image_url")
            if image_url and not image_url.startswith("data:"):
                image_url = await self._download_and_encode_image(image_url)

            # Build the request
            url = f"{self._server_url.rstrip('/')}/v1/responses"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "x-openclaw-agent-id": self._agent_id
            }
            user_field = (
                f"conv-{self._conversation_id}"
                if self._session_strategy == "by_conversation" and self._conversation_id
                else f"user-{self._user_id}"
            )
            input_field = self._build_input(message, image_url)
            body = {
                "model": self._model,
                "user": user_field,
                "input": input_field,
                "stream": False
            }

            timeout_cfg = aiohttp.ClientTimeout(total=self._timeout)
            # Log the request (truncate base64 to keep logs small)
            log_body = {**body}
            if isinstance(log_body.get("input"), list):
                log_body["input"] = "[multimodal input, truncated]"
            elif isinstance(log_body.get("input"), str) and len(log_body["input"]) > 500:
                log_body["input"] = log_body["input"][:500] + "..."
            logger.info(
                f"OpenClaw 请求: url={url}, agent_id={self._agent_id}, "
                f"has_image={bool(image_url)}, body={log_body}"
            )
            async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    execution_time = time.time() - start_time
                    if resp.status >= 400:
                        error_text = await resp.text()
                        return ToolResult.error_result(
                            error=f"OpenClaw HTTP {resp.status}: {error_text[:500]}",
                            error_code="OPENCLAW_HTTP_ERROR",
                            execution_time=execution_time
                        )
                    data = await resp.json()
                    text = self._extract_response(data)
                    display_text = self._format_result(text)
                    return ToolResult.success_result(
                        data=display_text,
                        execution_time=execution_time
                    )

        except aiohttp.ClientError as e:
            return ToolResult.error_result(
                error=f"OpenClaw 网络连接失败: {str(e)}",
                error_code="OPENCLAW_NETWORK_ERROR",
                execution_time=time.time() - start_time
            )
        except Exception as e:
            return ToolResult.error_result(
                error=f"OpenClaw 调用失败: {str(e)}",
                error_code="OPENCLAW_EXECUTION_ERROR",
                execution_time=time.time() - start_time
            )

    # ---------- Private helpers ----------
    def _extract_image_from_uploads(self) -> Optional[str]:
        """Extract an image URL from the user-uploaded files."""
        for f in self._uploaded_files:
            f_type = f.get("type", "")
            if f_type == "image":
                source = f.get("source", {})
                if source.get("type") == "base64":
                    media_type = source.get("media_type", "image/jpeg")
                    data = source.get("data", "")
                    return f"data:{media_type};base64,{data}"
                elif f.get("image"):
                    return f.get("image")
                elif f.get("url"):
                    return f.get("url")
            elif f_type == "image_url":
                return f.get("image_url", {}).get("url", "")
        return None

    async def _download_and_encode_image(self, image_url: str) -> str:
        """Download an image and convert it to a base64 data URI."""
        try:
            from PIL import Image
            MAX_RAW_SIZE = 4 * 1024 * 1024

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    image_url, allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return image_url
                    content_type = resp.headers.get("Content-Type", "image/jpeg")
                    if not content_type.startswith("image/"):
                        return image_url
                    img_bytes = await resp.read()

                    if len(img_bytes) > MAX_RAW_SIZE:
                        img = Image.open(BytesIO(img_bytes))
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        if max(img.size) > 2048:
                            img.thumbnail((2048, 2048), Image.LANCZOS)
                        buf = BytesIO()
                        img.save(buf, format="JPEG", quality=75, optimize=True)
                        img_bytes = buf.getvalue()
                        content_type = "image/jpeg"

                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    return f"data:{content_type};base64,{b64}"
        except Exception as e:
            logger.warning(f"OpenClaw 下载图片失败，使用原始 URL: {e}")
            return image_url

    def _build_input(self, message: str, image_url: Optional[str] = None):
        """Build the request input field: a multimodal structure when an image
        is present, plain text otherwise."""
        if not image_url:
            return message

        content_parts = [{"type": "input_text", "text": message}]
        if image_url.startswith("data:"):
            try:
                header, data = image_url.split(",", 1)
                media_type = header.split(":")[1].split(";")[0]
                content_parts.append({
                    "type": "input_image",
                    "source": {"type": "base64", "media_type": media_type, "data": data}
                })
            except (ValueError, IndexError):
                return message
        else:
            content_parts.append({
                "type": "input_image",
                "source": {"type": "url", "url": image_url}
            })

        return [{"type": "message", "role": "user", "content": content_parts}]

    def _extract_response(self, response_data: Dict[str, Any]) -> str:
        """Extract the text content from the OpenClaw response.

        OpenClaw /v1/responses only returns output_text type content.
        Image information (if any) is embedded into the text as Markdown links
        by the OpenClaw Skill.
        """
        output = response_data.get("output", [])
        texts = []
        for item in output:
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text" and content.get("text"):
                        texts.append(content["text"])
        return "\n".join(texts) if texts else str(response_data)

    @staticmethod
    def _format_result(text: str) -> str:
        """Format the result into an LLM-readable string."""
        return text or "（OpenClaw 返回了空内容）"
