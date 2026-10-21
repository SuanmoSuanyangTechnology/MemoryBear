"""
Image and video generation model wrappers

Supported providers:
- Volcano (Volcano Engine): uses volcenginesdkarkruntime
- OpenAI: uses the openai SDK
"""
import time
from typing import Any, Dict, Optional

from ..alert_metric_bridge import (
    report_model_gateway_failure,
    report_model_gateway_success,
)
from .base import RedBearModelConfig
from ..errors import BusinessException
from ..errors import BizCode
from ..usage_bridge import report_usage_failure, report_usage_success
from ..models.models_model import ModelProvider


def _get_ark_client(api_key: str, base_url: Optional[str] = None):
    from volcenginesdkarkruntime import Ark
    return Ark(api_key=api_key, base_url=base_url)


def _get_sequential_image_options(**kwargs):
    from volcenginesdkarkruntime.types.images.images import SequentialImageGenerationOptions
    return SequentialImageGenerationOptions(**kwargs)


def _get_content_generation_tool(**kwargs):
    from volcenginesdkarkruntime.types.images.images import ContentGenerationTool
    return ContentGenerationTool(**kwargs)


def _get_optimize_prompt_options(**kwargs):
    from volcenginesdkarkruntime.types.images.images import OptimizePromptOptions
    return OptimizePromptOptions(**kwargs)


class _ObservedGenerator:
    _config: RedBearModelConfig
    _usage_capability: str = "image"

    def _observed_call(self, operation: str, call):
        started = time.perf_counter()
        try:
            result = call()
        except Exception as exc:
            report_model_gateway_failure(self._config, operation, exc, started)
            report_usage_failure(
                self._config, self._usage_capability, operation, exc, started
            )
            raise
        report_model_gateway_success(self._config, operation, started)
        report_usage_success(
            self._config, self._usage_capability, operation, started, result=result
        )
        return result


class RedBearImageGenerator(_ObservedGenerator):
    """Image generation model wrapper."""

    _usage_capability = "image"
    
    def __init__(self, config: RedBearModelConfig):
        self._config = config
        self._client = self._create_client(config)
    
    def _create_client(self, config: RedBearModelConfig):
        """Create the client for the provider."""
        provider = config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            return _get_ark_client(api_key=config.api_key, base_url=config.base_url)
        # elif provider == ModelProvider.OPENAI:
        #     from openai import OpenAI
        #     return OpenAI(api_key=config.api_key, base_url=config.base_url)
        else:
            raise BusinessException(
                f"不支持的图片生成提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
    
    def generate(
        self,
        prompt: str,
        image: Optional[Any] = None,
        size: Optional[str] = "2K",
        output_format: str = "png",
        response_format: str = "url",
        watermark: bool = False,
        sequential_image_generation: Optional[str] = None,
        sequential_image_generation_options: Optional[Dict] = None,
        tools: Optional[list] = None,
        optimize_prompt_options: Optional[Dict] = None,
        stream: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate an image.

        Args:
            prompt: the prompt text
            image: reference image URL or list of URLs (image-to-image / multi-image fusion)
            size: image size, supporting "2K", "2048x2048", "1920x1080" etc. (at least 3686400 pixels)
            output_format: output format, e.g. "png", "jpg"
            response_format: return format, "url" or "b64_json"
            watermark: whether to add a watermark
            sequential_image_generation: sequential-image mode, "auto" or "disabled"
            sequential_image_generation_options: sequential-image options, e.g. {"max_images": 4}
            tools: tool list, e.g. [{"type": "web_search"}] for web-search-assisted generation
            optimize_prompt_options: prompt optimization options, e.g. {"mode": "fast"}
            stream: whether to use streaming generation
            **kwargs: other params

        Returns:
            the generation result
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            params = {
                "model": self._config.model_name,
                "prompt": prompt,
                "size": size,
                "output_format": output_format,
                "response_format": response_format,
                "watermark": watermark,
            }
            
            if image is not None:
                params["image"] = image
            
            if sequential_image_generation:
                params["sequential_image_generation"] = sequential_image_generation
                if sequential_image_generation_options:
                    params["sequential_image_generation_options"] = _get_sequential_image_options(
                        **sequential_image_generation_options
                    )
            
            if tools:
                params["tools"] = [_get_content_generation_tool(**tool) if isinstance(tool, dict) else tool for tool in tools]
            
            if optimize_prompt_options:
                params["optimize_prompt_options"] = _get_optimize_prompt_options(**optimize_prompt_options)
            
            if stream:
                params["stream"] = True
            
            params.update(kwargs)
            response = self._observed_call(
                "image.generate", lambda: self._client.images.generate(**params)
            )
            
        # elif provider == ModelProvider.OPENAI:
        #     response = self._client.images.generate(
        #         model=self._config.model_name,
        #         prompt=prompt,
        #         size=size,
        #         n=n,
        #         **kwargs
        #     )
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
        
        return response.model_dump() if hasattr(response, 'model_dump') else response
    
    async def agenerate(
        self,
        prompt: str,
        image: Optional[Any] = None,
        size: Optional[str] = "2K",
        output_format: str = "png",
        response_format: str = "url",
        watermark: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate an image asynchronously."""
        return self.generate(prompt, image, size, output_format, response_format, watermark, **kwargs)


class RedBearVideoGenerator(_ObservedGenerator):
    """Video generation model wrapper."""

    _usage_capability = "video"
    
    def __init__(self, config: RedBearModelConfig):
        self._config = config
        self._client = self._create_client(config)
    
    def _create_client(self, config: RedBearModelConfig):
        """Create the client for the provider."""
        provider = config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            return _get_ark_client(api_key=config.api_key, base_url=config.base_url)
        else:
            raise BusinessException(
                f"不支持的视频生成提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
    
    def generate(
        self,
        prompt: str,
        image_url: Optional[str] = None,
        first_frame_url: Optional[str] = None,
        last_frame_url: Optional[str] = None,
        reference_images: Optional[list] = None,
        draft_task_id: Optional[str] = None,
        duration: Optional[int] = None,
        frames: Optional[int] = None,
        ratio: Optional[str] = None,
        resolution: Optional[str] = None,
        generate_audio: bool = False,
        watermark: bool = False,
        camera_fixed: bool = False,
        seed: Optional[int] = None,
        return_last_frame: bool = False,
        service_tier: str = "default",
        execution_expires_after: Optional[int] = None,
        draft: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a video.

        Args:
            prompt: the prompt text
            image_url: first-frame image URL (image-to-video from the first frame)
            first_frame_url: first-frame image URL (image-to-video from first and last frames)
            last_frame_url: last-frame image URL (image-to-video from first and last frames)
            reference_images: list of reference image URLs (image-to-video from references)
            draft_task_id: draft task id (generate a full video from a draft)
            duration: video duration in seconds; mutually exclusive with frames
            frames: video frame count; mutually exclusive with duration
            ratio: video aspect ratio, e.g. "16:9", "9:16", "adaptive"
            resolution: video resolution, e.g. "720p", "1080p"
            generate_audio: whether to generate audio
            watermark: whether to add a watermark
            camera_fixed: whether to fix the camera
            seed: random seed
            return_last_frame: whether to return the last frame
            service_tier: service tier, "default" or "flex" (offline inference)
            execution_expires_after: task expiration time in seconds
            draft: whether to generate a draft
            **kwargs: other params

        Returns:
            the generation result (contains a task id; poll to obtain the outcome)
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            content = [{"type": "text", "text": prompt}]
            
            if draft_task_id:
                content = [{"type": "draft_task", "draft_task": {"id": draft_task_id}}]
            else:
                if image_url:
                    content.append({"type": "image_url", "image_url": {"url": image_url}})
                
                if first_frame_url:
                    content.append({"type": "image_url", "image_url": {"url": first_frame_url}, "role": "first_frame"})
                if last_frame_url:
                    content.append({"type": "image_url", "image_url": {"url": last_frame_url}, "role": "last_frame"})
                
                if reference_images:
                    for ref_url in reference_images:
                        content.append({"type": "image_url", "image_url": {"url": ref_url}, "role": "reference_image"})
            
            params = {"model": self._config.model_name, "content": content, "watermark": watermark}
            
            if duration:
                params["duration"] = duration
            if frames:
                params["frames"] = frames
            if ratio:
                params["ratio"] = ratio
            if resolution:
                params["resolution"] = resolution
            if generate_audio:
                params["generate_audio"] = generate_audio
            if camera_fixed:
                params["camera_fixed"] = camera_fixed
            if seed is not None:
                params["seed"] = seed
            if return_last_frame:
                params["return_last_frame"] = return_last_frame
            if service_tier != "default":
                params["service_tier"] = service_tier
            if execution_expires_after:
                params["execution_expires_after"] = execution_expires_after
            if draft:
                params["draft"] = draft
            
            params.update(kwargs)
            response = self._observed_call(
                "video.create", lambda: self._client.content_generation.tasks.create(**params)
            )
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
        
        return response.model_dump() if hasattr(response, 'model_dump') else response
    
    async def agenerate(
        self,
        prompt: str,
        image_url: Optional[str] = None,
        duration: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate a video asynchronously."""
        return self.generate(prompt, image_url=image_url, duration=duration, **kwargs)
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Query the status of a video generation task.

        Args:
            task_id: task id

        Returns:
            the task status information
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            response = self._observed_call(
                "video.get_task",
                lambda: self._client.content_generation.tasks.get(task_id=task_id),
            )
            return response.model_dump() if hasattr(response, 'model_dump') else response
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
    
    async def aget_task_status(self, task_id: str) -> Dict[str, Any]:
        """Query the task status asynchronously."""
        return self.get_task_status(task_id)
    
    def list_tasks(self, page_size: int = 10, status: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        List video generation tasks.

        Args:
            page_size: number of items per page
            status: task status filter, e.g. "succeeded", "failed", "pending"
            **kwargs: other params

        Returns:
            the task list
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            params = {"page_size": page_size}
            if status:
                params["status"] = status
            params.update(kwargs)
            response = self._observed_call(
                "video.list_tasks",
                lambda: self._client.content_generation.tasks.list(**params),
            )
            return response.model_dump() if hasattr(response, 'model_dump') else response
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
    
    def delete_task(self, task_id: str) -> None:
        """
        Delete or cancel a video generation task.

        Args:
            task_id: task id
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            self._observed_call(
                "video.delete_task",
                lambda: self._client.content_generation.tasks.delete(task_id=task_id),
            )
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
