"""Synchronous client for the MinerU V3 task API."""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from ....bootstrap import get_settings

LOGGER = logging.getLogger(__name__)
COMPLETED_STATUSES = {"completed", "complete", "success", "succeeded", "done", "finished"}
FAILED_STATUSES = {"failed", "fail", "error", "errored", "canceled", "cancelled"}


@dataclass(frozen=True)
class MinerUV3Image:
    name: str
    image: Image.Image
    binary: bytes
    content_type: str
    file_ext: str


@dataclass(frozen=True)
class MinerUV3Result:
    markdown: str
    images: dict[str, MinerUV3Image]


class MinerUV3Client:
    def __init__(
        self,
        api_server: str | None = None,
        request_timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        poll_timeout_seconds: float | None = None,
        session=requests,
    ) -> None:
        settings = get_settings()
        self.api_server = (api_server or settings.mineru_v3_apiserver).strip().rstrip("/")
        self.request_timeout_seconds = (
            request_timeout_seconds or settings.mineru_v3_request_timeout_seconds
        )
        self.poll_interval_seconds = (
            poll_interval_seconds or settings.mineru_v3_poll_interval_seconds
        )
        self.poll_timeout_seconds = poll_timeout_seconds or settings.mineru_v3_poll_timeout_seconds
        self._session = session

    def parse_to_markdown(
        self,
        file_name: str,
        binary: bytes,
        start_page_id: int,
        end_page_id: int,
        callback: Callable | None = None,
    ) -> str:
        return self.parse(
            file_name,
            binary,
            start_page_id,
            end_page_id,
            callback,
            return_images=False,
        ).markdown

    def parse(
        self,
        file_name: str,
        binary: bytes,
        start_page_id: int,
        end_page_id: int,
        callback: Callable | None = None,
        return_images: bool = True,
    ) -> MinerUV3Result:
        if not self.api_server:
            raise RuntimeError("MinerU V3 API server is not configured")
        if not binary:
            raise RuntimeError("MinerU V3 received empty input")
        self._callback(callback, 0.20, "Submit MinerU V3 task.")
        task_id = self._submit_task(
            file_name,
            binary,
            start_page_id,
            end_page_id,
            return_images,
        )
        self._poll_task(task_id, callback)
        file_result = self._extract_file_result(self._fetch_result(task_id), file_name)
        markdown = self._extract_markdown(file_result)
        images = self._extract_images(file_result) if return_images else {}
        self._callback(callback, 0.70, "MinerU V3 Markdown extracted.")
        LOGGER.info("MinerU V3 result extracted chars=%s images=%s", len(markdown), len(images))
        return MinerUV3Result(markdown=markdown, images=images)

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._session.request(
                method,
                f"{self.api_server}{path}",
                timeout=self.request_timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            status_suffix = f" status={status}" if status is not None else ""
            raise RuntimeError(f"MinerU V3 request failed{status_suffix}") from None
        except ValueError:
            raise RuntimeError("MinerU V3 returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError("MinerU V3 returned an invalid response shape")
        return payload

    def _submit_task(
        self,
        file_name: str,
        binary: bytes,
        start_page_id: int,
        end_page_id: int,
        return_images: bool,
    ) -> str:
        payload = self._request_json(
            "POST",
            "/tasks",
            files={
                "files": (
                    Path(file_name).name,
                    BytesIO(binary),
                    "application/octet-stream",
                )
            },
            data=self._build_form_data(start_page_id, end_page_id, return_images),
            headers={"Accept": "application/json"},
        )
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("MinerU V3 task response omitted task_id")
        return str(task_id)

    def _poll_task(self, task_id: str, callback: Callable | None) -> None:
        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            payload = self._request_json(
                "GET",
                f"/tasks/{task_id}",
                headers={"Accept": "application/json"},
            )
            status = str(payload.get("status", "")).strip().lower()
            self._callback(callback, 0.30, f"MinerU V3 task status: {status or 'unknown'}.")
            if status in COMPLETED_STATUSES:
                self._callback(callback, 0.60, "MinerU V3 task completed.")
                return
            if status in FAILED_STATUSES:
                raise RuntimeError(f"MinerU V3 task failed with status={status}")
            if time.monotonic() >= deadline:
                raise RuntimeError("MinerU V3 task polling timed out")
            time.sleep(self.poll_interval_seconds)

    def _fetch_result(self, task_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/tasks/{task_id}/result",
            headers={"Accept": "application/json"},
        )

    @staticmethod
    def _build_form_data(
        start_page_id: int,
        end_page_id: int,
        return_images: bool,
    ) -> dict[str, str]:
        return {
            "lang_list": "ch",
            "backend": "hybrid-engine",
            "effort": "medium",
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "image_analysis": "false",
            "server_url": "",
            "return_md": "true",
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_content_list": "false",
            "return_images": "true" if return_images else "false",
            "response_format_zip": "false",
            "return_original_file": "false",
            "client_side_output_generation": "false",
            "start_page_id": str(start_page_id),
            "end_page_id": str(end_page_id),
        }

    @staticmethod
    def _extract_file_result(payload: dict[str, Any], file_name: str) -> dict[str, Any]:
        results = payload.get("results")
        file_result = results.get(Path(file_name).stem) if isinstance(results, dict) else None
        if not isinstance(file_result, dict):
            raise RuntimeError("MinerU V3 result omitted the requested file")
        return file_result

    @staticmethod
    def _extract_markdown(file_result: dict[str, Any]) -> str:
        markdown = file_result.get("md_content")
        if not isinstance(markdown, str) or not markdown.strip():
            raise RuntimeError("MinerU V3 returned empty Markdown")
        return markdown

    def _extract_images(self, file_result: dict[str, Any]) -> dict[str, MinerUV3Image]:
        raw_images = file_result.get("images") or {}
        if not isinstance(raw_images, dict):
            LOGGER.warning("MinerU V3 image payload ignored because it is not a mapping")
            return {}
        images: dict[str, MinerUV3Image] = {}
        for name, payload in raw_images.items():
            decoded = self._decode_image_payload(str(name), payload)
            if decoded is not None:
                images[Path(str(name)).name] = decoded
        return images

    @staticmethod
    def _decode_image_payload(name: str, payload: Any) -> MinerUV3Image | None:
        if not isinstance(payload, str) or not payload:
            return None
        content_type = None
        encoded = payload
        if payload.startswith("data:") and "," in payload:
            metadata, encoded = payload.split(",", 1)
            content_type = metadata[5:].split(";", 1)[0].strip() or None
        try:
            binary = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(binary)) as source:
                image_format = source.format
                image = source.convert("RGB").copy()
        except (binascii.Error, OSError, ValueError):
            LOGGER.warning("MinerU V3 invalid image payload skipped")
            return None
        content_type = content_type or mimetypes.guess_type(name)[0]
        content_type = (
            content_type
            or Image.MIME.get(image_format or "")
            or "application/octet-stream"
        )
        file_ext = Path(name).suffix.lower() or mimetypes.guess_extension(content_type) or ".png"
        if file_ext == ".jpe":
            file_ext = ".jpg"
        return MinerUV3Image(
            name=Path(name).name,
            image=image,
            binary=binary,
            content_type=content_type,
            file_ext=file_ext,
        )

    @staticmethod
    def _callback(callback: Callable | None, progress: float, message: str) -> None:
        if callback:
            callback(progress, message)


__all__ = ["MinerUV3Client", "MinerUV3Image", "MinerUV3Result"]
