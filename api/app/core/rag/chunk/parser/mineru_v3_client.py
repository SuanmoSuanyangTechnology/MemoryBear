import base64
import binascii
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image


LOGGER = logging.getLogger(__name__)

DEFAULT_MINERU_V3_APISERVER = "http://183.147.142.122:18000"
COMPLETED_STATUSES = {"completed", "complete", "success", "succeeded", "done", "finished"}
FAILED_STATUSES = {"failed", "fail", "error", "errored", "canceled", "cancelled"}


@dataclass
class MinerUV3Result:
    markdown: str
    images: dict[str, Image.Image]


def _env_float(name: str, default: float | int) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning("[MinerUV3] invalid %s=%r, using default %s", name, raw, default)
        return float(default)


class MinerUV3Client:
    def __init__(
        self,
        api_server: str | None = None,
        request_timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        poll_timeout_seconds: float | None = None,
    ):
        raw_api_server = (
            api_server
            if api_server is not None
            else os.environ.get("MINERU_V3_APISERVER", DEFAULT_MINERU_V3_APISERVER)
        )
        self.api_server = raw_api_server.strip().rstrip("/")
        self.request_timeout_seconds = (
            request_timeout_seconds
            if request_timeout_seconds is not None
            else _env_float("MINERU_V3_REQUEST_TIMEOUT_SECONDS", 600.0)
        )
        self.poll_interval_seconds = poll_interval_seconds
        if self.poll_interval_seconds is None:
            self.poll_interval_seconds = _env_float("MINERU_V3_POLL_INTERVAL_SECONDS", 2)
        self.poll_timeout_seconds = (
            poll_timeout_seconds
            if poll_timeout_seconds is not None
            else _env_float("MINERU_V3_POLL_TIMEOUT_SECONDS", 1800.0)
        )

    def parse_to_markdown(
        self,
        file_name: str,
        binary: bytes,
        start_page_id: int,
        end_page_id: int,
        callback: Callable | None = None,
    ) -> str:
        return self.parse(file_name, binary, start_page_id, end_page_id, callback).markdown

    def parse(
        self,
        file_name: str,
        binary: bytes,
        start_page_id: int,
        end_page_id: int,
        callback: Callable | None = None,
    ) -> MinerUV3Result:
        if not self.api_server:
            raise RuntimeError("[MinerUV3] service unavailable: MINERU_V3_APISERVER is not configured")
        if binary is None:
            raise RuntimeError("[MinerUV3] empty input binary")

        LOGGER.info("[MinerUV3] request start: file_name=%s, parse_method=auto", file_name)
        task_id = self._submit_task(file_name, binary, start_page_id, end_page_id, callback)
        self._poll_task(task_id, callback)
        result_payload = self._fetch_result(task_id)
        file_result = self._extract_file_result(result_payload, file_name)
        markdown = self._extract_markdown(file_result, file_name)
        images = self._extract_images(file_result)
        LOGGER.info("[MinerUV3] markdown extracted: chars=%s", len(markdown))
        if callback:
            callback(0.70, "MinerU V3 markdown extracted.")
        return MinerUV3Result(markdown=markdown, images=images)

    def _submit_task(
        self,
        file_name: str,
        binary: bytes,
        start_page_id: int,
        end_page_id: int,
        callback: Callable | None,
    ) -> str:
        url = f"{self.api_server}/tasks"
        data = self._build_form_data(start_page_id, end_page_id)
        files = {
            "files": (
                Path(file_name).name,
                BytesIO(binary),
                "application/octet-stream",
            )
        }
        headers = {"Accept": "application/json"}
        if callback:
            callback(0.20, "Submit MinerU V3 task.")
        try:
            response = requests.post(url, files=files, data=data, headers=headers, timeout=self.request_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"[MinerUV3] service unavailable: POST /tasks failed: {exc}") from exc

        task_id = self._extract_task_id(payload)
        LOGGER.info("[MinerUV3] task accepted: task_id=%s", task_id)
        return task_id

    def _poll_task(self, task_id: str, callback: Callable | None) -> None:
        deadline = time.monotonic() + self.poll_timeout_seconds
        url = f"{self.api_server}/tasks/{task_id}"
        headers = {"Accept": "application/json"}
        while True:
            try:
                response = requests.get(url, headers=headers, timeout=self.request_timeout_seconds)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(f"[MinerUV3] service unavailable: GET /tasks/{task_id} failed: {exc}") from exc

            status = str(payload.get("status", "")).lower()
            LOGGER.info("[MinerUV3] task polling: task_id=%s, status=%s", task_id, status)
            if callback:
                callback(0.30, f"MinerU V3 task status: {status or 'unknown'}.")

            if status in COMPLETED_STATUSES:
                LOGGER.info("[MinerUV3] task completed: task_id=%s", task_id)
                if callback:
                    callback(0.60, "MinerU V3 task completed, fetching result.")
                return

            if status in FAILED_STATUSES:
                message = payload.get("message") or payload.get("error") or payload.get("detail") or ""
                raise RuntimeError(f"[MinerUV3] task failed: task_id={task_id} status={status} message={message}")

            if time.monotonic() >= deadline:
                raise RuntimeError(f"[MinerUV3] task polling timeout: task_id={task_id} status={status}")

            time.sleep(self.poll_interval_seconds)

    def _fetch_result(self, task_id: str) -> dict[str, Any]:
        url = f"{self.api_server}/tasks/{task_id}/result"
        headers = {"Accept": "application/json"}
        try:
            response = requests.get(url, headers=headers, timeout=self.request_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"[MinerUV3] service unavailable: GET /tasks/{task_id}/result failed: {exc}") from exc
        return payload

    def _build_form_data(self, start_page_id: int, end_page_id: int) -> dict[str, str]:
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
            "return_images": "true",
            "response_format_zip": "false",
            "return_original_file": "false",
            "client_side_output_generation": "false",
            "start_page_id": str(start_page_id),
            "end_page_id": str(end_page_id),
        }

    def _extract_task_id(self, payload: dict[str, Any]) -> str:
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError(f"[MinerUV3] missing task_id in task response: keys={list(payload.keys())}")
        return str(task_id)

    def _extract_file_result(self, payload: dict[str, Any], file_name: str) -> dict[str, Any]:
        file_stem = Path(file_name).stem
        results = payload.get("results")
        if not isinstance(results, dict):
            raise RuntimeError(f"[MinerUV3] missing markdown content in parse result: results missing for {file_stem}")
        file_result = results.get(file_stem)
        if not isinstance(file_result, dict):
            raise RuntimeError(f"[MinerUV3] missing markdown content in parse result: results.{file_stem} missing")
        return file_result

    def _extract_markdown(self, file_result: dict[str, Any], file_name: str) -> str:
        file_stem = Path(file_name).stem
        markdown = file_result.get("md_content")
        if markdown is None:
            raise RuntimeError(f"[MinerUV3] missing markdown content in parse result: results.{file_stem}.md_content missing")
        if not isinstance(markdown, str) or not markdown.strip():
            raise RuntimeError("[MinerUV3] empty markdown content")
        return markdown

    def _extract_images(self, file_result: dict[str, Any]) -> dict[str, Image.Image]:
        raw_images = file_result.get("images") or {}
        if not isinstance(raw_images, dict):
            LOGGER.warning("[MinerUV3] images field is not a dict, skipping image payloads")
            return {}

        images: dict[str, Image.Image] = {}
        for name, payload in raw_images.items():
            image = self._decode_image_payload(str(name), payload)
            if image is not None:
                images[Path(str(name)).name] = image
        return images

    def _decode_image_payload(self, name: str, payload: Any) -> Image.Image | None:
        if not isinstance(payload, str) or not payload:
            LOGGER.warning("[MinerUV3] image payload skipped: name=%s reason=non-string", name)
            return None
        encoded = payload.split(",", 1)[1] if payload.startswith("data:") and "," in payload else payload
        try:
            binary = base64.b64decode(encoded, validate=True)
            return Image.open(BytesIO(binary)).convert("RGB")
        except (binascii.Error, OSError, ValueError) as exc:
            LOGGER.warning("[MinerUV3] image payload skipped: name=%s error=%s", name, exc)
            return None
