"""ModelScope MCP market client.

Thin boundary around the modelscope SDK: every upstream/network failure is
translated to ``BizCode.SERVICE_UNAVAILABLE`` and token probes to
``BizCode.INVALID_PARAMETER`` so no raw SDK exception escapes the service.
``MCPApi.login`` writes the SDK's token cache under ``~/.modelscope`` -- a
side effect inherited from the monolith, not something this wrapper adds.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from modelscope.hub.errors import RequestError, raise_for_http_status
from modelscope.hub.mcp_api import MCPApi, MCPApiError

from ..errors import BizCode, BusinessException

logger = logging.getLogger(__name__)


class ModelscopeMcpClient:
    """ModelScope MCP market operations used by the MCP config service."""

    def list_servers(
        self,
        token: str,
        page: int,
        pagesize: int,
        keywords: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        try:
            api = MCPApi()
            api.login(token)
            body = {
                "filter": {},
                "page_number": page,
                "page_size": pagesize,
                "search": keywords,
            }
            cookies = api.get_cookies(token)
            headers = api.builder_headers(api.headers)
            headers["Authorization"] = f"Bearer {token}"
            r = api.session.put(
                url=api.mcp_base_url,
                headers=headers,
                json=body,
                cookies=cookies,
            )
            raise_for_http_status(r)
            data = api._handle_response(r)
        except (
            requests.exceptions.RequestException,
            MCPApiError,
            RequestError,
            KeyError,
        ) as exc:
            raise BusinessException(
                f"Failed to get MCP servers: {exc}",
                BizCode.SERVICE_UNAVAILABLE,
                cause=exc,
            ) from exc
        return data.get("total_count", 0), data.get("mcp_server_list", [])

    def list_operational_servers(self, token: str) -> list[dict[str, Any]]:
        try:
            api = MCPApi()
            api.login(token)
            url = f"{api.mcp_base_url}/operational"
            headers = api.builder_headers(api.headers)
            headers["Authorization"] = f"Bearer {token}"
            cookies = api.get_cookies(access_token=token, cookies_required=True)
            r = api.session.get(url, headers=headers, cookies=cookies)
            raise_for_http_status(r)
            data = api._handle_response(r)
        except (
            requests.exceptions.RequestException,
            MCPApiError,
            RequestError,
            KeyError,
        ) as exc:
            raise BusinessException(
                f"Failed to get operational MCP servers: {exc}",
                BizCode.SERVICE_UNAVAILABLE,
                cause=exc,
            ) from exc
        return data.get("mcp_server_list", [])

    def get_server(self, token: str, server_id: str) -> dict[str, Any]:
        try:
            api = MCPApi()
            api.login(token)
            return api.get_mcp_server(server_id=server_id, token=token)
        except BusinessException:
            raise
        except Exception as exc:
            raise BusinessException(
                f"Failed to get MCP server: {exc}",
                BizCode.SERVICE_UNAVAILABLE,
                cause=exc,
            ) from exc

    def validate_token(self, token: str) -> None:
        """Raise INVALID_PARAMETER unless a one-item listing succeeds."""
        try:
            self.list_servers(token, 1, 1, None)
        except Exception as exc:
            raise BusinessException(
                "Unable to access ModelScope MCP market with the provided "
                f"token: {exc}",
                BizCode.INVALID_PARAMETER,
                cause=exc,
            ) from exc
