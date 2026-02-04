import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import httpx
# import filetypes # TODO: File support (Feature)
from httpx import AsyncClient, Response, Timeout

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.enums import HttpRequestMethod, HttpErrorHandle, HttpAuthType, HttpContentType
from app.core.workflow.nodes.http_request.config import HttpRequestNodeConfig, HttpRequestNodeOutput
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__file__)


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

    def _build_content(self, variable_pool: VariablePool) -> dict[str, Any]:
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
                for item in self.typed_config.body.data:
                    if item.type == "text":
                        data[self._render_template(item.key, variable_pool)] = self._render_template(item.value, variable_pool)
                    elif item.type == "file":
                        # TODO: File support (Feature)
                        pass
                content["data"] = data
            case HttpContentType.BINARY:
                # TODO: File support (Feature)
                pass
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
                        **self._build_content(variable_pool)
                    )
                    resp.raise_for_status()
                    logger.info(f"Node {self.node_id}: HTTP request succeeded")
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
                        return "ERROR"
                raise RuntimeError("http request failed")
