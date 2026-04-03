
from typing import Any, Dict, List, Optional, Union
from langchain_core.embeddings import Embeddings

from app.core.models.base import RedBearModelConfig, get_provider_embedding_class, RedBearModelFactory
from app.models.models_model import ModelProvider


class RedBearEmbeddings(Embeddings):
    """统一的 Embedding 类，自动支持多模态（根据 provider 判断）"""
    
    def __init__(self, config: RedBearModelConfig):
        self._config = config
        self._is_volcano = config.provider.lower() == ModelProvider.VOLCANO
        
        if self._is_volcano:
            # 火山引擎使用 Ark SDK
            self._client = self._create_volcano_client(config)
            self._model = None
        else:
            # 其他 provider 使用 LangChain
            self._model = self._create_model(config)
            self._client = None

    def _create_model(self, config: RedBearModelConfig) -> Embeddings:
        """根据配置创建 LangChain 模型"""
        embedding_class = get_provider_embedding_class(config.provider)
        provider = config.provider.lower()
        # Embedding models only need connection params, never LLM-specific ones
        # (e.g. enable_thinking, model_kwargs) — build params directly.
        if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK]:
            import httpx
            params = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": httpx.Timeout(timeout=config.timeout, connect=60.0),
                "max_retries": config.max_retries,
            }
        elif provider == ModelProvider.DASHSCOPE:
            params = {
                "model": config.model_name,
                "dashscope_api_key": config.api_key,
                "max_retries": config.max_retries,
            }
        elif provider == ModelProvider.OLLAMA:
            params = {
                "model": config.model_name,
                "base_url": config.base_url,
            }
        elif provider == ModelProvider.BEDROCK:
            params = RedBearModelFactory.get_model_params(config)
        else:
            params = RedBearModelFactory.get_model_params(config)
        return embedding_class(**params)
    
    def _create_volcano_client(self, config: RedBearModelConfig):
        """创建火山引擎客户端"""
        from volcenginesdkarkruntime import Ark
        return Ark(api_key=config.api_key, base_url=config.base_url)

    # ==================== LangChain 标准接口 ====================
    
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（LangChain 标准接口）"""
        if self._is_volcano:
            # 火山引擎多模态 Embedding
            contents = [{"type": "text", "text": text} for text in texts]
            response = self._client.multimodal_embeddings.create(
                model=self._config.model_name,
                input=contents,
                encoding_format="float"
            )
            return [response.data.embedding]
        else:
            # 其他 provider
            return self._model.embed_documents(texts)

    def embed_query(self, text: str) -> List[float]:
        """单个文本向量化（LangChain 标准接口）"""
        if self._is_volcano:
            # 火山引擎多模态 Embedding
            result = self.embed_documents([text])
            return result[0] if result else []
        else:
            # 其他 provider
            return self._model.embed_query(text)
    
    # ==================== 多模态扩展方法 ====================
    
    def embed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """
        多模态向量化（仅火山引擎支持）
        
        Args:
            contents: 内容列表，格式：
                - 文本: {"type": "text", "text": "..."}
                - 图片: {"type": "image_url", "image_url": {"url": "..."}}
                - 视频: {"type": "video_url", "video_url": {"url": "..."}}
            **kwargs: 其他参数
            
        Returns:
            向量列表
        """
        if not self._is_volcano:
            raise NotImplementedError(
                f"多模态 Embedding 仅支持火山引擎，当前 provider: {self._config.provider}"
            )
        
        response = self._client.multimodal_embeddings.create(
            model=self._config.model_name,
            input=contents,
            **kwargs
        )
        return [response.data.embedding]
    
    async def aembed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """异步多模态向量化"""
        # 火山引擎 SDK 暂不支持异步，使用同步方法
        return self.embed_multimodal(contents, **kwargs)
    
    def embed_text(self, text: str, **kwargs) -> List[float]:
        """文本向量化（便捷方法）"""
        if self._is_volcano:
            result = self.embed_multimodal(
                [{"type": "text", "text": text}],
                **kwargs
            )
            return result[0] if result else []
        else:
            return self.embed_query(text)
    
    def embed_image(self, image_url: str, **kwargs) -> List[float]:
        """图片向量化（仅火山引擎支持）"""
        if not self._is_volcano:
            raise NotImplementedError(
                f"图片向量化仅支持火山引擎，当前 provider: {self._config.provider}"
            )
        
        result = self.embed_multimodal(
            [{"type": "image_url", "image_url": {"url": image_url}}],
            **kwargs
        )
        return result[0] if result else []
    
    def embed_video(self, video_url: str, **kwargs) -> List[float]:
        """视频向量化（仅火山引擎支持）"""
        if not self._is_volcano:
            raise NotImplementedError(
                f"视频向量化仅支持火山引擎，当前 provider: {self._config.provider}"
            )
        
        result = self.embed_multimodal(
            [{"type": "video_url", "video_url": {"url": video_url}}],
            **kwargs
        )
        return result[0] if result else []
    
    def embed_batch(
        self,
        items: List[Union[str, Dict[str, Any]]],
        **kwargs
    ) -> List[List[float]]:
        """
        批量向量化（支持混合类型）
        
        Args:
            items: 可以是字符串列表或内容字典列表
            **kwargs: 其他参数
            
        Returns:
            向量列表
        """
        # 如果全是字符串，使用标准方法
        if all(isinstance(item, str) for item in items):
            return self.embed_documents(items)
        
        # 如果包含字典，需要多模态支持
        if not self._is_volcano:
            raise NotImplementedError(
                f"混合类型批量向量化仅支持火山引擎，当前 provider: {self._config.provider}"
            )
        
        # 标准化输入格式
        contents = []
        for item in items:
            if isinstance(item, str):
                contents.append({"type": "text", "text": item})
            elif isinstance(item, dict):
                contents.append(item)
            else:
                raise ValueError(f"不支持的输入类型: {type(item)}")
        
        return self.embed_multimodal(contents, **kwargs)
    
    # ==================== 工具方法 ====================
    
    def is_multimodal_supported(self) -> bool:
        """检查是否支持多模态"""
        return self._is_volcano
    
    def get_provider(self) -> str:
        """获取 provider"""
        return self._config.provider


# 保留 RedBearMultimodalEmbeddings 作为别名，向后兼容
RedBearMultimodalEmbeddings = RedBearEmbeddings
