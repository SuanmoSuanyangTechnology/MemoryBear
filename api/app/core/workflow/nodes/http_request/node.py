import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import httpx
# import filetypes # TODO: File support (Feature)
from httpx import AsyncClient, Response, Timeout

from app.core.workflow.nodes import BaseNode, WorkflowState
from app.core.workflow.nodes.enums import HttpRequestMethod, HttpErrorHandle, HttpAuthType, HttpContentType
from app.core.workflow.nodes.http_request.config import HttpRequestNodeConfig, HttpRequestNodeOutput

logger = logging.getLogger(__file__)

DEFAULT_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")


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
        self.typed_config = HttpRequestNodeConfig(**self.config)

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

    def _build_auth(self, state: WorkflowState) -> dict[str, str]:
        """
        Build authentication-related HTTP headers.

        Authentication values support template rendering based on
        the current workflow runtime state.

        Args:
            state: Current workflow runtime state.

        Returns:
            A dictionary of HTTP headers used for authentication.
        """
        api_key = self._render_template(self.typed_config.auth.api_key, state)
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

    def _build_header(self, state: WorkflowState) -> dict[str, str]:
        """
        Build HTTP request headers.

        Both header keys and values support runtime template rendering.
        """
        headers = {
            "user-agent": DEFAULT_USER_AGENT
        }
        for key, value in self.typed_config.headers.items():
            headers[self._render_template(key, state)] = self._render_template(value, state)
        return headers

    def _build_params(self, state: WorkflowState) -> dict[str, str]:
        """
        Build URL query parameters.

        Parameter keys and values support runtime template rendering.
        """
        params = {}
        for key, value in self.typed_config.params.items():
            params[self._render_template(key, state)] = self._render_template(value, state)
        return params

    def _build_content(self, state) -> dict[str, Any]:
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
                    json.dumps(self.typed_config.body.data), state
                ))
            case HttpContentType.FROM_DATA:
                data = {}
                for item in self.typed_config.body.data:
                    if item.type == "text":
                        data[self._render_template(item.key, state)] = self._render_template(item.value, state)
                    elif item.type == "file":
                        # TODO: File support (Feature)
                        pass
                content["data"] = data
            case HttpContentType.BINARY:
                # TODO: File support (Feature)
                pass
            case HttpContentType.WWW_FORM:
                content["data"] = json.loads(self._render_template(
                    json.dumps(self.typed_config.body.data), state
                ))

            case HttpContentType.RAW:
                content["content"] = self._render_template(self.typed_config.body.data, state)
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

    def build_conditional_edge_expressions(self):
        """
        Build conditional edge expressions for workflow branching.

        When the HTTP error handling strategy is set to `BRANCH`,
        this node exposes a single conditional output labeled "ERROR".
        The workflow engine uses this output to create an explicit
        error-handling branch for downstream nodes.

        Returns:
            list[str]:
                - ["ERROR"] if error handling strategy is BRANCH
                - An empty list if no conditional branching is required
        """
        if self.typed_config.error_handle.method == HttpErrorHandle.BRANCH:
            return ["ERROR"]
        return []

    async def execute(self, state: WorkflowState) -> dict | str:
        """
        Execute the HTTP request node.

        Execution flow:
        1. Initialize AsyncClient with configured options
        2. Perform HTTP request with retry mechanism
        3. Apply configured error handling strategy on failure

        Args:
            state: Current workflow runtime state.

        Returns:
            - dict: Serialized HttpRequestNodeOutput on success
            - str: Branch identifier (e.g. "ERROR") when branching is enabled
        """
        async with httpx.AsyncClient(
                verify=self.typed_config.verify_ssl,
                timeout=self._build_timeout(),
                headers=self._build_header(state) | self._build_auth(state),
                params=self._build_params(state),
                follow_redirects=True
        ) as client:
            retries = self.typed_config.retry.max_attempts
            while retries > 0:
                try:
                    request_func = self._get_client_method(client)
                    resp = await request_func(
                        url=self._render_template(self.typed_config.url, state),
                        **self._build_content(state)
                    )
                    resp.raise_for_status()
                    return HttpRequestNodeOutput(
                        body=resp.text,
                        status_code=resp.status_code,
                        headers=resp.headers,
                    ).model_dump()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    logger.error(f"HTTP request node exception: {e}")
                    retries -= 1
                    if retries > 0:
                        await asyncio.sleep(self.typed_config.retry.retry_interval / 1000)
            else:
                match self.typed_config.error_handle.method:
                    case HttpErrorHandle.NONE:
                        return HttpRequestNodeOutput(
                            body="",
                            status_code=resp.status_code,
                            headers=resp.headers,
                        ).model_dump()
                    case HttpErrorHandle.DEFAULT:
                        return self.typed_config.error_handle.default.model_dump()
                    case HttpErrorHandle.BRANCH:
                        return "ERROR"
