"""Minimal Feishu client copied from the legacy authentication path."""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ....utils.datetime_utils import parse_timestamp_to_utc_naive
from .exceptions import FeishuAPIError, FeishuAuthError
from .models import FileInfo


def _timestamp(value: Any) -> datetime | None:
    try:
        return parse_timestamp_to_utc_naive(int(value))
    except (TypeError, ValueError, OSError):
        return None


class FeishuAPIClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        api_base_url: str = "https://open.feishu.cn/open-apis",
        timeout: int = 30,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
    ):
        self.app_id = app_id
        self._app_secret = app_secret
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any | None = None
        self._access_token: str | None = None

    async def __aenter__(self) -> FeishuAPIClient:
        self._client = self._client_factory(
            base_url=self.api_base_url,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def get_tenant_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if self._client is None:
            raise FeishuAuthError("HTTP client not initialized")
        try:
            response = await self._client.post(
                "/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self._app_secret},
            )
        except httpx.HTTPError as exc:
            raise FeishuAuthError("Feishu authentication request failed") from exc
        body = response.json()
        if body.get("code") != 0 or not body.get("tenant_access_token"):
            raise FeishuAuthError("Feishu authentication failed", str(body.get("code")))
        self._access_token = str(body["tenant_access_token"])
        return self._access_token

    async def list_folder_files(
        self,
        folder_token: str,
        page_token: str | None = None,
    ) -> tuple[list[FileInfo], str | None]:
        if self._client is None:
            raise FeishuAPIError("HTTP client not initialized")
        token = await self.get_tenant_access_token()
        params = {"page_size": 200, "folder_token": folder_token}
        if page_token:
            params["page_token"] = page_token
        try:
            response = await self._client.get(
                "/drive/v1/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise FeishuAPIError("Feishu folder request failed") from exc
        body = response.json()
        if body.get("code") != 0:
            raise FeishuAPIError("Feishu API request failed", str(body.get("code")))
        data = body.get("data") or {}
        files = [
            FileInfo(
                token=str(item.get("token") or ""),
                name=str(item.get("name") or ""),
                type=str(item.get("type") or ""),
                created_time=_timestamp(item.get("created_time")),
                modified_time=_timestamp(item.get("modified_time")),
                owner_id=str(item.get("owner_id") or ""),
                url=str(item.get("url") or ""),
            )
            for item in data.get("files", [])
            if isinstance(item, dict)
        ]
        return files, data.get("next_page_token")

    async def list_all_folder_files(
        self,
        folder_token: str,
        recursive: bool = True,
    ) -> list[FileInfo]:
        result = []
        page_token = None
        while True:
            files, page_token = await self.list_folder_files(folder_token, page_token)
            result.extend(files)
            if not page_token:
                break
        if recursive:
            for folder in [item for item in result if item.type == "folder"]:
                result.extend(await self.list_all_folder_files(folder.token, recursive=True))
        return result

    async def download_document(self, document: FileInfo, save_dir: str) -> str:
        """Download or export one reachable Feishu document."""

        try:
            token = await self.get_tenant_access_token()
            if self._client is None:
                raise FeishuAPIError("HTTP client not initialized")
            if document.type in {"doc", "docx", "sheet", "bitable"}:
                return await self._export_file(document, token, save_dir)
            if document.type in {"file", "slides"}:
                return await self._download_file(document, token, save_dir)
            raise FeishuAPIError("Unsupported Feishu document type")
        except (FeishuAPIError, FeishuAuthError):
            raise
        except Exception:
            raise FeishuAPIError("Feishu document download failed") from None

    async def _export_file(
        self,
        document: FileInfo,
        access_token: str,
        save_dir: str,
    ) -> str:
        extension = {
            "doc": "doc",
            "docx": "docx",
            "sheet": "xlsx",
            "bitable": "xlsx",
        }.get(document.type, "pdf")
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            response = await self._client.post(
                "/drive/v1/export_tasks",
                json={
                    "file_extension": extension,
                    "token": document.token,
                    "type": document.type,
                },
                headers=headers,
            )
            body = response.json()
            if body.get("code") != 0:
                raise FeishuAPIError(
                    "Feishu document export request failed",
                    str(body.get("code")),
                )
            ticket = (body.get("data") or {}).get("ticket")
            if not ticket:
                raise FeishuAPIError("Feishu document export ticket is missing")

            file_token = None
            for _attempt in range(10):
                response = await self._client.get(
                    f"/drive/v1/export_tasks/{ticket}",
                    params={"token": document.token},
                    headers=headers,
                )
                body = response.json()
                if body.get("code") != 0:
                    raise FeishuAPIError(
                        "Feishu document export status failed",
                        str(body.get("code")),
                    )
                file_token = ((body.get("data") or {}).get("result") or {}).get(
                    "file_token"
                )
                if file_token:
                    break
                await asyncio.sleep(2)
            if not file_token:
                raise FeishuAPIError("Feishu document export timed out")

            response = await self._client.get(
                f"/drive/v1/export_tasks/file/{file_token}/download",
                headers=headers,
            )
            response.raise_for_status()
            return await asyncio.to_thread(
                self._write_binary_file,
                save_dir,
                f"{document.name}.{extension}",
                response.content,
            )
        except FeishuAPIError:
            raise
        except httpx.HTTPError:
            raise FeishuAPIError("Feishu document export download failed") from None
        except Exception:
            raise FeishuAPIError("Feishu document export failed") from None

    async def _download_file(
        self,
        document: FileInfo,
        access_token: str,
        save_dir: str,
    ) -> str:
        try:
            response = await self._client.get(
                f"/drive/v1/files/{document.token}/download",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            disposition = response.headers.get("Content-Disposition")
            filename = None
            if disposition:
                match = re.search(r"filename\*=([^']*)''([^;]+)", disposition)
                if match:
                    filename = urllib.parse.unquote(match.group(2))
                if not filename:
                    match = re.search(r'filename="([^"]+)"', disposition)
                    if match:
                        filename = match.group(1)
            if not filename:
                filename = f"{document.name}.pdf"
            return await asyncio.to_thread(
                self._write_binary_file,
                save_dir,
                filename,
                response.content,
            )
        except httpx.HTTPError:
            raise FeishuAPIError("Feishu file download failed") from None
        except Exception:
            raise FeishuAPIError("Feishu file download failed") from None

    @staticmethod
    def _write_binary_file(save_dir: str, filename: str, content: bytes) -> str:
        root = Path(save_dir).resolve()
        basename = Path(filename.replace("\\", "/")).name
        basename = re.sub(r'[\\/:*?"<>|]', "_", basename)
        if basename in {"", ".", ".."}:
            basename = "download"
        path = (root / basename).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise FeishuAPIError("Feishu download path is outside the save directory") from None
        if path.parent != root:
            raise FeishuAPIError("Feishu download path is outside the save directory")
        if path.exists():
            path.unlink()
        path.write_bytes(content)
        return str(path)


__all__ = ["FeishuAPIClient"]
