"""Feishu API client for document operations."""

import asyncio
import os
import re
from typing import Optional, Tuple, List
from datetime import datetime, timedelta
import httpx
from cachetools import TTLCache
import urllib.parse

from app.core.rag.integrations.feishu.exceptions import (
    FeishuAuthError,
    FeishuAPIError,
    FeishuNotFoundError,
    FeishuPermissionError,
    FeishuRateLimitError,
    FeishuNetworkError,
)
from app.core.rag.integrations.feishu.models import FileInfo
from app.core.rag.integrations.feishu.retry import with_retry


class FeishuAPIClient:
    """Feishu API client for document synchronization."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        api_base_url: str = "https://open.feishu.cn/open-apis",
        timeout: int = 30,
        max_retries: int = 3
    ):
        """
        Initialize Feishu API client.
        
        Args:
            app_id: Feishu application ID
            app_secret: Feishu application secret
            api_base_url: Feishu API base URL
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_base_url = api_base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._http_client: Optional[httpx.AsyncClient] = None
        self._token_cache: TTLCache = TTLCache(maxsize=1, ttl=7200 - 300)  # 2 hours - 5 minutes
        self._token_lock = asyncio.Lock()

    async def __aenter__(self):
        """Async context manager entry."""
        self._http_client = httpx.AsyncClient(
            base_url=self.api_base_url,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._http_client:
            await self._http_client.aclose()

    async def get_tenant_access_token(self) -> str:
        """
        Get tenant access token with caching.
        
        Returns:
            Access token string
            
        Raises:
            FeishuAuthError: If authentication fails
        """
        # Check cache first
        cached_token = self._token_cache.get("access_token")
        if cached_token:
            return cached_token

        # Use lock to prevent concurrent token requests
        async with self._token_lock:
            # Double-check cache after acquiring lock
            cached_token = self._token_cache.get("access_token")
            if cached_token:
                return cached_token

            # Request new token
            try:
                if not self._http_client:
                    raise FeishuAuthError("HTTP client not initialized")

                response = await self._http_client.post(
                    "/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret
                    }
                )

                data = response.json()

                if data.get("code") != 0:
                    error_msg = data.get("msg", "Unknown error")
                    raise FeishuAuthError(
                        f"Authentication failed: {error_msg}",
                        error_code=str(data.get("code")),
                        details=data
                    )

                token = data.get("tenant_access_token")
                if not token:
                    raise FeishuAuthError("No access token in response")

                # Cache the token
                self._token_cache["access_token"] = token

                return token

            except httpx.HTTPError as e:
                raise FeishuAuthError(f"HTTP error during authentication: {str(e)}")
            except Exception as e:
                if isinstance(e, FeishuAuthError):
                    raise
                raise FeishuAuthError(f"Unexpected error during authentication: {str(e)}")

    @with_retry
    async def list_folder_files(
            self,
            folder_token: str,
            page_token: Optional[str] = None
    ) -> Tuple[List[FileInfo], Optional[str]]:
        """
        Get list of files in a folder with pagination support.
        
        Args:
            folder_token: Folder token
            page_token: Page token for pagination
            
        Returns:
            Tuple of (list of FileInfo, next page token)
            
        Raises:
            FeishuAPIError: If API call fails
            FeishuNotFoundError: If folder not found
            FeishuPermissionError: If permission denied
        """
        try:
            token = await self.get_tenant_access_token()

            if not self._http_client:
                raise FeishuAPIError("HTTP client not initialized")

            # Build request parameters
            params = {"page_size": 200, "folder_token": folder_token}
            if page_token:
                params["page_token"] = page_token

            # Make API request
            response = await self._http_client.get(
                f"/drive/v1/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"}
            )

            data = response.json()
            # print(f"get files: {data}")

            # Handle errors
            if data.get("code") != 0:
                error_code = data.get("code")
                error_msg = data.get("msg", "Unknown error")

                if error_code == 404 or error_code == 230005:
                    raise FeishuNotFoundError(
                        f"Folder not found: {error_msg}",
                        error_code=str(error_code),
                        details=data
                    )
                elif error_code == 403 or error_code == 230003:
                    raise FeishuPermissionError(
                        f"Permission denied: {error_msg}",
                        error_code=str(error_code),
                        details=data
                    )
                else:
                    raise FeishuAPIError(
                        f"API error: {error_msg}",
                        error_code=str(error_code),
                        details=data
                    )

            # Parse response
            files_data = data.get("data", {}).get("files", [])
            next_page_token = data.get("data", {}).get("next_page_token", None)

            # Convert to FileInfo objects
            files = []
            for file_data in files_data:
                try:
                    file_info = FileInfo(
                        token=file_data.get("token", ""),
                        name=file_data.get("name", ""),
                        type=file_data.get("type", ""),
                        created_time=datetime.fromtimestamp(int(file_data.get("created_time", 0))),
                        modified_time=datetime.fromtimestamp(int(file_data.get("modified_time", 0))),
                        owner_id=file_data.get("owner_id", ""),
                        url=file_data.get("url", "")
                    )
                    files.append(file_info)
                except (ValueError, TypeError) as e:
                    # Skip invalid file entries
                    continue

            return files, next_page_token

        except httpx.HTTPError as e:
            raise FeishuAPIError(f"HTTP error: {str(e)}")
        except Exception as e:
            if isinstance(e, (FeishuAPIError, FeishuNotFoundError, FeishuPermissionError)):
                raise
            raise FeishuAPIError(f"Unexpected error: {str(e)}")

    async def list_all_folder_files(
            self,
            folder_token: str,
            recursive: bool = True
    ) -> List[FileInfo]:
        """
        Get all files in a folder, handling pagination automatically.

        Args:
            folder_token: Folder token
            recursive: Whether to recursively get files from subfolders

        Returns:
            List of all FileInfo objects

        Raises:
            FeishuAPIError: If API call fails
        """
        all_files = []
        page_token = None

        # Get all files with pagination
        while True:
            files, page_token = await self.list_folder_files(folder_token, page_token)
            all_files.extend(files)

            if not page_token:
                break

        # Recursively get files from subfolders if requested
        if recursive:
            subfolders = [f for f in all_files if f.type == "folder"]
            for subfolder in subfolders:
                try:
                    subfolder_files = await self.list_all_folder_files(
                        subfolder.token,
                        recursive=True
                    )
                    all_files.extend(subfolder_files)
                except Exception:
                    # Continue with other folders if one fails
                    continue

        return all_files

    @with_retry
    async def download_document(
            self,
            document: FileInfo,
            save_dir: str
    ) -> str:
        """
        download document content.
        
        Args:
            document: Document FileInfo
            save_dir: save dir
            
        Returns:
            file_full_path
            
        Raises:
            FeishuAPIError: If API call fails
            FeishuNotFoundError: If document not found
            FeishuPermissionError: If permission denied
        """
        try:
            token = await self.get_tenant_access_token()

            if not self._http_client:
                raise FeishuAPIError("HTTP client not initialized")

            # Different API endpoints for different document types
            if document.type == "doc" or document.type == "docx" or document.type == "sheet" or document.type == "bitable":
                return await self._export_file(document, token, save_dir)
            elif document.type == "file" or document.type == "slides":
                return await self._download_file(document, token, save_dir)
            else:
                raise FeishuAPIError(f"Unsupported document type: {document.type}")

        except Exception as e:
            if isinstance(e, (FeishuAPIError, FeishuNotFoundError, FeishuPermissionError)):
                raise
            raise FeishuAPIError(f"Unexpected error: {str(e)}")

    async def _export_file(self, document: FileInfo, access_token: str, save_dir: str) -> str:
        """export file for feishu online file type."""
        try:
            # 1.创建导出任务
            file_extension = "pdf"
            match document.type:
                case "doc":
                    file_extension = "doc"
                case "docx":
                    file_extension = "docx"
                case "sheet":
                    file_extension = "xlsx"
                case "bitable":
                    file_extension = "xlsx"
                case _:
                    file_extension = "pdf"
            response = await self._http_client.post(
                "/drive/v1/export_tasks",
                json={
                    "file_extension": file_extension,
                    "token": document.token,
                    "type": document.type
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            data = response.json()
            print(f"1.创建导出任务: {data}")

            if data.get("code") != 0:
                error_code = data.get("code")
                error_msg = data.get("msg", "Unknown error")
                raise FeishuAPIError(
                    f"API error: {error_msg}",
                    error_code=str(error_code),
                    details=data
                )

            ticket = data.get("data", {}).get("ticket", None)
            if not ticket:
                raise FeishuAuthError("No ticket in response")

            # 2.轮序查询导出任务结果
            max_retries = 10  # 最大轮询次数
            poll_interval = 2  # 每次轮询间隔时间（秒）
            file_token = None
            for attempt in range(max_retries):
                # 查询导出任务
                response = await self._http_client.get(
                    f"/drive/v1/export_tasks/{ticket}",
                    params={"token": document.token},
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                data = response.json()
                print(f"2. 尝试查询导出任务结果 (第{attempt + 1}次): {data}")

                if data.get("code") != 0:
                    error_code = data.get("code")
                    error_msg = data.get("msg", "Unknown error")
                    raise FeishuAPIError(
                        f"API error: {error_msg}",
                        error_code=str(error_code),
                        details=data,
                    )

                # 检查导出任务结果
                file_token = data.get("data", {}).get("result", {}).get("file_token", None)
                if file_token:
                    # 如果导出任务成功生成 file_token，则退出轮询
                    break

                # 如果结果还没准备好，等待一段时间再进行下一次轮询
                await asyncio.sleep(poll_interval)

            if not file_token:
                raise FeishuAPIError("Export task did not complete within the allowed time")

            # 3.下载导出任务
            response = await self._http_client.get(
                f"/drive/v1/export_tasks/file/{file_token}/download",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()
            print(f'3.下载导出任务: {response.headers.get("Content-Disposition")}')

            file_full_path = os.path.join(save_dir, document.name + "." + file_extension)
            if os.path.exists(file_full_path):
                os.remove(file_full_path)  # Delete a single file
            with open(file_full_path, "wb") as file:
                file.write(response.content)

            return file_full_path

        except httpx.HTTPError as e:
            raise FeishuAPIError(f"HTTP error: {str(e)}")
        except Exception as e:
            raise FeishuAPIError(f"Unexpected error during file download: {str(e)}")

    async def _download_file(self, document: FileInfo, access_token: str, save_dir: str) -> str:
        """download file for file type."""
        try:
            response = await self._http_client.get(
                f"/drive/v1/files/{document.token}/download",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()

            filename_header = response.headers.get("Content-Disposition")

            # 最终的文件名（初始化为 None）
            filename = None
            if filename_header:
                # 优先解析 filename* 格式
                match = re.search(r"filename\*=([^']*)''([^;]+)", filename_header)
                if match:
                    # 使用 `filename*` 提取（已编码）
                    encoding = match.group(1)  # 编码部分（如 UTF-8）
                    encoded_filename = match.group(2)  # 文件名部分
                    filename = urllib.parse.unquote(encoded_filename)  # 解码 URL 编码的文件名

                # 如果 `filename*` 不存在，回退到解析 `filename`
                if not filename:
                    match = re.search(r'filename="([^"]+)"', filename_header)
                    if match:
                        filename = match.group(1)
            # 如果文件名仍为 None，则使用默认文件名
            if not filename:
                filename = f"{document.name}.pdf"
            # 确保文件名合法，替换非法字符
            filename = re.sub(r'[\/:*?"<>|]', '_', filename)

            file_full_path = os.path.join(save_dir, filename)
            if os.path.exists(file_full_path):
                os.remove(file_full_path) # Delete a single file
            with open(file_full_path, "wb") as file:
                file.write(response.content)

            return file_full_path

        except httpx.HTTPError as e:
            raise FeishuAPIError(f"HTTP error: {str(e)}")
        except Exception as e:
            raise FeishuAPIError(f"Unexpected error during file download: {str(e)}")
