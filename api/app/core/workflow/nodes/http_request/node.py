import asyncio
import json
import logging
import mimetypes
import uuid
import imghdr
from email.message import Message
from typing import Any, Callable, Coroutine

import httpx
from httpx import AsyncClient, Response, Timeout
import magic

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.enums import HttpRequestMethod, HttpErrorHandle, HttpAuthType, HttpContentType
from app.core.workflow.nodes.http_request.config import HttpRequestNodeConfig, HttpRequestNodeOutput
from app.core.workflow.utils.file_processer import mime_to_file_type
from app.core.workflow.variable.base_variable import VariableType, FileObject
from app.core.workflow.variable.variable_objects import FileVariable, ArrayVariable
from app.schemas import FileType, TransferMethod

logger = logging.getLogger(__file__)


class HttpResponse:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.headers = dict(response.headers)

        self._is_file: bool | None = None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def content_disposition(self) -> Message | None:
        content_disposition = self.headers.get("content-disposition", "")
        if content_disposition:
            msg = Message()
            msg["content-disposition"] = content_disposition
            return msg
        return None

    @property
    def is_file(self) -> bool:
        if self._is_file is not None:
            return self._is_file
        content_type = self.content_type.split(";")[0].strip().lower()

        parsed_content_disposition = self.content_disposition
        if parsed_content_disposition:
            disp_type = parsed_content_disposition.get_content_disposition()
            filename = parsed_content_disposition.get_filename()
            if disp_type == "attachment" or filename:
                self._is_file = True
                return True

        if content_type.startswith("text/") and "csv" not in content_type:
            return False

        if content_type.startswith("application/"):
            if any(
                    text_type in content_type
                    for text_type in {"json", "xml", "javascript", "x-www-form-urlencoded", "yaml", "graphql"}
            ):
                self._is_file = False
                return False
            try:
                content_sample = self.response.content[:1024]
                content_sample.decode("utf-8")
                text_markers = (b"{", b"[", b"<", b"function", b"var ", b"const ", b"let ")
                if any(marker in content_sample for marker in text_markers):
                    return False
            except UnicodeDecodeError:
                self._is_file = True
                return True

        main_type, _ = mimetypes.guess_type("dummy" + (mimetypes.guess_extension(content_type) or ""))
        if main_type:
            self._is_file = main_type.split("/")[0] in ("application", "image", "audio", "video")
            return self._is_file
        self._is_file = any(media_type in content_type for media_type in ("image/", "audio/", "video/"))
        return self._is_file

    @property
    def is_image(self):
        if self.is_file:
            kind = imghdr.what(None, h=self.response.content)
            return kind is not None
        return False

    @property
    def url(self) -> str:
        return str(self.response.url)

    @property
    def body(self) -> str:
        if self.is_file:
            return f"{'!' if self.is_image else ''}[file]({self.url})"
        return self.response.text

    @staticmethod
    def get_file_type(file_bytes) -> tuple[FileType | None, str | None]:
        mime = magic.from_buffer(file_bytes, mime=True)

        if mime.startswith("image"):
            return FileType.IMAGE, mime
        elif mime.startswith("video"):
            return FileType.VIDEO, mime
        elif mime.startswith("audio"):
            return FileType.AUDIO, mime
        elif mime in ["application/pdf",
                      "application/msword",
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                      "application/vnd.ms-excel",
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      "text/plain"]:
            return FileType.DOCUMENT, mime
        return None, None

    @property
    def files(self) -> list[FileObject]:
        file_type, mime_type = self.get_file_type(self.response.content)
        origin_file_type = mime_to_file_type(mime_type)
        if self.is_file and file_type and origin_file_type:
            file_obj = FileObject(
                type=file_type,
                url=self.url,
                transfer_method=TransferMethod.REMOTE_URL.value,
                origin_file_type=origin_file_type,
                file_id=None,
                is_file=True
            )
            file_obj.set_content(self.response.content)
            return [
                file_obj
            ]
        return []


class HttpRequestNode(BaseNode):
    """
    HTTP Request Workflow Node.

    This node executes an HTTP request as part of a workflow execution.
    It supports:
    - Multiple HTTP methods (GET, POST, PUT, DELETE, PATCH, HEAD)
    - Multiple authentication strategies
    - Multiple request body content types
    - Retry mechanism with configurable interval
    - Flexible error handling strategies

    The execution result is returned as a serialized HttpRequestNodeOutput,
    or a branch identifier string when error branching is enabled.
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: HttpRequestNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "body": VariableType.STRING,
            "status_code": VariableType.NUMBER,
            "headers": VariableType.OBJECT,
            "files": VariableType.ARRAY_FILE,
            "output": VariableType.STRING
        }

    def _build_timeout(self) -> Timeout:
        """
        Build httpx Timeout configuration.

        All four timeout dimensions are explicitly defined to avoid
        implicit defaults that may lead to unpredictable behavior
        in production environments.
        """
        timeout = httpx.Timeout(
            connect=self.typed_config.timeouts.connect_timeout,
            read=self.typed_config.timeouts.read_timeout,
            write=self.typed_config.timeouts.write_timeout,
            pool=5
        )
        return timeout

    def _build_auth(self, variable_pool: VariablePool) -> dict[str, str]:
        """
        Build authentication-related HTTP headers.

        Authentication values support template rendering based on
        the current workflow runtime state.

        Args:
            variable_pool: Variable Pool

        Returns:
            A dictionary of HTTP headers used for authentication.
        """
        api_key = self._render_template(self.typed_config.auth.api_key, variable_pool)
        match self.typed_config.auth.auth_type:
            case HttpAuthType.NONE:
                return {}
            case HttpAuthType.BASIC:
                return {
                    "Authorization": f"Basic {api_key}",
                }
            case HttpAuthType.BEARER:
                return {
                    "Authorization": f"Bearer {api_key}",
                }
            case HttpAuthType.CUSTOM:
                return {
                    self.typed_config.auth.header: api_key
                }
            case _:
                raise RuntimeError(f"Auth type not supported: {self.typed_config.auth.auth_type}")

    def _build_header(self, variable_pool: VariablePool) -> dict[str, str]:
        """
        Build HTTP request headers.

        Both header keys and values support runtime template rendering.
        """
        headers = {}
        for key, value in self.typed_config.headers.items():
            headers[self._render_template(key, variable_pool)] = self._render_template(value, variable_pool)
        return headers

    def _build_params(self, variable_pool: VariablePool) -> dict[str, str]:
        """
        Build URL query parameters.

        Parameter keys and values support runtime template rendering.
        """
        params = {}
        for key, value in self.typed_config.params.items():
            params[self._render_template(key, variable_pool)] = self._render_template(value, variable_pool)
        return params

    async def _build_content(self, variable_pool: VariablePool) -> dict[str, Any]:
        """
        Build HTTP request body arguments for httpx request methods.

        The returned dictionary is directly unpacked into the httpx
        request call (e.g., json=, data=, content=).

        Returns:
            A dictionary containing httpx-compatible request body arguments.
        """
        content = {}
        match self.typed_config.body.content_type:
            case HttpContentType.NONE:
                return {}
            case HttpContentType.JSON:
                content["json"] = json.loads(self._render_template(
                    self.typed_config.body.data, variable_pool
                ))
            case HttpContentType.FROM_DATA:
                data = {}
                content["files"] = {}
                for item in self.typed_config.body.data:
                    if item.type == "text":
                        data[self._render_template(item.key, variable_pool)] = self._render_template(item.value,
                                                                                                     variable_pool)
                    elif item.type == "file":
                        content["files"][self._render_template(item.key, variable_pool)] = (
                            uuid.uuid4().hex,
                            await variable_pool.get_instance(item.value).get_content()
                        )
                content["data"] = data
            case HttpContentType.BINARY:
                content["files"] = []
                file_instence = variable_pool.get_instance(self.typed_config.body.data)
                if isinstance(file_instence, ArrayVariable):
                    for v in file_instence.value:
                        if isinstance(v, FileVariable):
                            content["files"].append(
                                (
                                    "files", (uuid.uuid4().hex, await v.get_content())
                                )
                            )
                elif isinstance(file_instence, FileVariable):
                    content["files"].append(
                        (
                            "file", (uuid.uuid4().hex, await file_instence.get_content())
                        )
                    )

            case HttpContentType.WWW_FORM:
                content["data"] = json.loads(self._render_template(
                    json.dumps(self.typed_config.body.data), variable_pool
                ))

            case HttpContentType.RAW:
                content["content"] = self._render_template(self.typed_config.body.data, variable_pool)
            case _:
                raise RuntimeError(f"Content type not supported: {self.typed_config.body.content_type}")
        return content

    def _get_client_method(self, client: AsyncClient) -> Callable[..., Coroutine[Any, Any, Response]]:
        """
        Resolve the httpx AsyncClient method based on configured HTTP method.
        """
        match self.typed_config.method:
            case HttpRequestMethod.GET:
                return client.get
            case HttpRequestMethod.POST:
                return client.post
            case HttpRequestMethod.PUT:
                return client.put
            case HttpRequestMethod.DELETE:
                return client.delete
            case HttpRequestMethod.PATCH:
                return client.patch
            case HttpRequestMethod.HEAD:
                return client.head
            case _:
                raise RuntimeError(f"HttpRequest method not supported: {self.typed_config.method}")

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict | str:
        """
        Execute the HTTP request node.

        Execution flow:
        1. Initialize AsyncClient with configured options
        2. Perform HTTP request with retry mechanism
        3. Apply configured error handling strategy on failure

        Args:
            state: Current workflow runtime state.
            variable_pool: Variable Pool

        Returns:
            - dict: Serialized HttpRequestNodeOutput on success
            - str: Branch identifier (e.g. "ERROR") when branching is enabled
        """
        self.typed_config = HttpRequestNodeConfig(**self.config)
        async with httpx.AsyncClient(
                verify=self.typed_config.verify_ssl,
                timeout=self._build_timeout(),
                headers=self._build_header(variable_pool) | self._build_auth(variable_pool),
                params=self._build_params(variable_pool),
                follow_redirects=True
        ) as client:
            retries = self.typed_config.retry.max_attempts
            while retries > 0:
                try:
                    request_func = self._get_client_method(client)
                    resp = await request_func(
                        url=self._render_template(self.typed_config.url, variable_pool),
                        **(await self._build_content(variable_pool))
                    )
                    resp.raise_for_status()
                    logger.info(f"Node {self.node_id}: HTTP request succeeded")
                    response = HttpResponse(resp)
                    return HttpRequestNodeOutput(
                        body=response.body,
                        status_code=resp.status_code,
                        headers=resp.headers,
                        files=response.files
                    ).model_dump()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    logger.error(f"HTTP request node exception: {e}")
                    retries -= 1
                    if retries > 0:
                        await asyncio.sleep(self.typed_config.retry.retry_interval / 1000)
                    elif self.typed_config.error_handle.method == HttpErrorHandle.NONE:
                        raise e
                except Exception as e:
                    raise RuntimeError(f"HTTP request node exception: {e}")
            else:
                match self.typed_config.error_handle.method:
                    case HttpErrorHandle.DEFAULT:
                        logger.warning(
                            f"Node {self.node_id}: HTTP request failed, returning default result"
                        )
                        return self.typed_config.error_handle.default.model_dump()
                    case HttpErrorHandle.BRANCH:
                        logger.warning(
                            f"Node {self.node_id}: HTTP request failed, switching to error handling branch"
                        )
                        return {"output": "ERROR"}
                raise RuntimeError("http request failed")
