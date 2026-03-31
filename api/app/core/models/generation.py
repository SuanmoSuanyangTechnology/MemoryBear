"""
图片和视频生成模型封装

支持的 Provider:
- Volcano (火山引擎): 使用 volcenginesdkarkruntime
- OpenAI: 使用 openai SDK
"""
from typing import Any, Dict, Optional

from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime.types.images.images import (
    SequentialImageGenerationOptions,
    ContentGenerationTool,
    OptimizePromptOptions
)

from app.core.models.base import RedBearModelConfig
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.models.models_model import ModelProvider


class RedBearImageGenerator:
    """图片生成模型封装"""
    
    def __init__(self, config: RedBearModelConfig):
        self._config = config
        self._client = self._create_client(config)
    
    def _create_client(self, config: RedBearModelConfig):
        """根据 provider 创建客户端"""
        provider = config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            return Ark(api_key=config.api_key, base_url=config.base_url)
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
        生成图片
        
        Args:
            prompt: 提示词
            image: 参考图片URL或URL列表（图文生图/多图融合）
            size: 图片尺寸，支持 "2K", "2048x2048", "1920x1080" 等（至少3686400像素）
            output_format: 输出格式，如 "png", "jpg"
            response_format: 返回格式，"url" 或 "b64_json"
            watermark: 是否添加水印
            sequential_image_generation: 组图生成模式，"auto" 或 "disabled"
            sequential_image_generation_options: 组图生成选项，如 {"max_images": 4}
            tools: 工具列表，如 [{"type": "web_search"}] 用于联网搜索生图
            optimize_prompt_options: 提示词优化选项，如 {"mode": "fast"}
            stream: 是否使用流式生成
            **kwargs: 其他参数
            
        Returns:
            生成结果
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
                    params["sequential_image_generation_options"] = SequentialImageGenerationOptions(
                        **sequential_image_generation_options
                    )
            
            if tools:
                params["tools"] = [ContentGenerationTool(**tool) if isinstance(tool, dict) else tool for tool in tools]
            
            if optimize_prompt_options:
                params["optimize_prompt_options"] = OptimizePromptOptions(**optimize_prompt_options)
            
            if stream:
                params["stream"] = True
            
            params.update(kwargs)
            response = self._client.images.generate(**params)
            
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
        """异步生成图片"""
        return self.generate(prompt, image, size, output_format, response_format, watermark, **kwargs)


class RedBearVideoGenerator:
    """视频生成模型封装"""
    
    def __init__(self, config: RedBearModelConfig):
        self._config = config
        self._client = self._create_client(config)
    
    def _create_client(self, config: RedBearModelConfig):
        """根据 provider 创建客户端"""
        provider = config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            return Ark(api_key=config.api_key, base_url=config.base_url)
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
        生成视频
        
        Args:
            prompt: 提示词
            image_url: 首帧图片URL（图生视频-基于首帧）
            first_frame_url: 首帧图片URL（图生视频-基于首尾帧）
            last_frame_url: 尾帧图片URL（图生视频-基于首尾帧）
            reference_images: 参考图片URL列表（图生视频-基于参考图）
            draft_task_id: Draft任务ID（基于Draft生成正式视频）
            duration: 视频时长（秒），与frames二选一
            frames: 视频帧数，与duration二选一
            ratio: 视频比例，如 "16:9", "9:16", "adaptive"
            resolution: 视频分辨率，如 "720p", "1080p"
            generate_audio: 是否生成音频
            watermark: 是否添加水印
            camera_fixed: 是否固定镜头
            seed: 随机种子
            return_last_frame: 是否返回最后一帧
            service_tier: 服务层级，"default" 或 "flex"（离线推理）
            execution_expires_after: 任务过期时间（秒）
            draft: 是否生成样片
            **kwargs: 其他参数
            
        Returns:
            生成结果（包含任务ID，需要轮询获取结果）
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
            response = self._client.content_generation.tasks.create(**params)
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
        """异步生成视频"""
        return self.generate(prompt, image_url=image_url, duration=duration, **kwargs)
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        查询视频生成任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务状态信息
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            response = self._client.content_generation.tasks.get(task_id=task_id)
            return response.model_dump() if hasattr(response, 'model_dump') else response
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
    
    async def aget_task_status(self, task_id: str) -> Dict[str, Any]:
        """异步查询任务状态"""
        return self.get_task_status(task_id)
    
    def list_tasks(self, page_size: int = 10, status: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        查询视频生成任务列表
        
        Args:
            page_size: 每页数量
            status: 任务状态筛选，如 "succeeded", "failed", "pending"
            **kwargs: 其他参数
            
        Returns:
            任务列表
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            params = {"page_size": page_size}
            if status:
                params["status"] = status
            params.update(kwargs)
            response = self._client.content_generation.tasks.list(**params)
            return response.model_dump() if hasattr(response, 'model_dump') else response
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
    
    def delete_task(self, task_id: str) -> None:
        """
        删除或取消视频生成任务
        
        Args:
            task_id: 任务ID
        """
        provider = self._config.provider.lower()
        
        if provider == ModelProvider.VOLCANO:
            self._client.content_generation.tasks.delete(task_id=task_id)
        else:
            raise BusinessException(
                f"不支持的提供商: {provider}",
                code=BizCode.PROVIDER_NOT_SUPPORTED
            )
