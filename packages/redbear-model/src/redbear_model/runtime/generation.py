"""Image and video generation runtime."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from redbear_model.contracts import ModelProvider, ResolvedModelConfig
from redbear_model.errors import UnsupportedModelProviderError
from redbear_model.providers.volcengine import (
    build_content_generation_tool,
    build_optimize_prompt_options,
    build_sequential_image_options,
    load_ark_class,
)
from redbear_model.telemetry import (
    ModelTelemetry,
    NoOpModelTelemetry,
    report_failure_safely,
)


def _dump(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response


class _ObservedGenerator:
    def _observe(self, operation: str, call):
        started = time.perf_counter()
        try:
            return call()
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=operation,
                exc=exc,
                started_at=started,
            )
            raise


class RedBearImageGenerator(_ObservedGenerator):
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client: Any | None = None,
        telemetry: ModelTelemetry | None = None,
    ):
        self._config = config
        self._telemetry = telemetry or NoOpModelTelemetry()
        self._client = client if client is not None else self._create_client(config)

    @staticmethod
    def _create_client(config: ResolvedModelConfig):
        if config.provider is not ModelProvider.VOLCANO:
            raise UnsupportedModelProviderError(config.provider.value)
        return load_ark_class()(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
        )

    def generate(
        self,
        prompt: str,
        image: Any | None = None,
        size: str | None = "2K",
        output_format: str = "png",
        response_format: str = "url",
        watermark: bool = False,
        sequential_image_generation: str | None = None,
        sequential_image_generation_options: dict[str, Any] | None = None,
        tools: list[Any] | None = None,
        optimize_prompt_options: dict[str, Any] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._config.model_name,
            "prompt": prompt,
            "size": size,
            "output_format": output_format,
            "response_format": response_format,
            "watermark": watermark,
            **kwargs,
        }
        if image is not None:
            params["image"] = image
        if sequential_image_generation:
            params["sequential_image_generation"] = sequential_image_generation
            if sequential_image_generation_options:
                params["sequential_image_generation_options"] = (
                    build_sequential_image_options(
                        sequential_image_generation_options
                    )
                )
        if tools:
            params["tools"] = [
                build_content_generation_tool(tool)
                if isinstance(tool, dict)
                else tool
                for tool in tools
            ]
        if optimize_prompt_options:
            params["optimize_prompt_options"] = build_optimize_prompt_options(
                optimize_prompt_options
            )
        if stream:
            params["stream"] = True
        if callable(self._client):
            response = self._observe(
                "image.generate",
                lambda: self._client(**params),
            )
        else:
            response = self._observe(
                "image.generate",
                lambda: self._client.images.generate(**params),
            )
        return _dump(response)

    async def agenerate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.generate, prompt, **kwargs)


class RedBearVideoGenerator(_ObservedGenerator):
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client: Any | None = None,
        telemetry: ModelTelemetry | None = None,
    ):
        self._config = config
        self._telemetry = telemetry or NoOpModelTelemetry()
        self._client = client if client is not None else self._create_client(config)

    @staticmethod
    def _create_client(config: ResolvedModelConfig):
        if config.provider is not ModelProvider.VOLCANO:
            raise UnsupportedModelProviderError(config.provider.value)
        return load_ark_class()(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
        )

    def generate(
        self,
        prompt: str,
        image_url: str | None = None,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_images: list[str] | None = None,
        draft_task_id: str | None = None,
        duration: int | None = None,
        frames: int | None = None,
        ratio: str | None = None,
        resolution: str | None = None,
        generate_audio: bool = False,
        watermark: bool = False,
        camera_fixed: bool = False,
        seed: int | None = None,
        return_last_frame: bool = False,
        service_tier: str = "default",
        execution_expires_after: int | None = None,
        draft: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if draft_task_id:
            content: list[dict[str, Any]] = [
                {"type": "draft_task", "draft_task": {"id": draft_task_id}}
            ]
        else:
            content = [{"type": "text", "text": prompt}]
            if image_url:
                content.append(
                    {"type": "image_url", "image_url": {"url": image_url}}
                )
            if first_frame_url:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": first_frame_url},
                        "role": "first_frame",
                    }
                )
            if last_frame_url:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": last_frame_url},
                        "role": "last_frame",
                    }
                )
            for reference_url in reference_images or []:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": reference_url},
                        "role": "reference_image",
                    }
                )
        params: dict[str, Any] = {
            "model": self._config.model_name,
            "content": content,
            "watermark": watermark,
            **kwargs,
        }
        if duration is not None:
            params["duration"] = duration
        if frames is not None:
            params["frames"] = frames
        if ratio:
            params["ratio"] = ratio
        if resolution:
            params["resolution"] = resolution
        if generate_audio:
            params["generate_audio"] = True
        if camera_fixed:
            params["camera_fixed"] = True
        if seed is not None:
            params["seed"] = seed
        if return_last_frame:
            params["return_last_frame"] = True
        if service_tier != "default":
            params["service_tier"] = service_tier
        if execution_expires_after is not None:
            params["execution_expires_after"] = execution_expires_after
        if draft:
            params["draft"] = True
        response = self._observe(
            "video.create",
            lambda: self._client.content_generation.tasks.create(**params),
        )
        return _dump(response)

    async def agenerate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.generate, prompt, **kwargs)

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        response = self._observe(
            "video.get_task",
            lambda: self._client.content_generation.tasks.get(task_id=task_id),
        )
        return _dump(response)

    async def aget_task_status(self, task_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.get_task_status, task_id)

    def list_tasks(
        self,
        page_size: int = 10,
        status: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size, **kwargs}
        if status:
            params["status"] = status
        response = self._observe(
            "video.list_tasks",
            lambda: self._client.content_generation.tasks.list(**params),
        )
        return _dump(response)

    def delete_task(self, task_id: str) -> None:
        self._observe(
            "video.delete_task",
            lambda: self._client.content_generation.tasks.delete(task_id=task_id),
        )
