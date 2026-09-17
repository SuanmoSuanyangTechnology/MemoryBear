import asyncio
import time
from typing import Any, Callable, Dict, List, Union

from langchain_core.embeddings import Embeddings

from redbear_model import ResolvedModelConfig
from app.core.alert_metric_bridge import (
    report_model_gateway_failure,
    report_model_gateway_failure_async,
    report_model_gateway_success,
    report_model_gateway_success_async,
)
from app.core.config import settings
from app.core.models.base import RedBearModelConfig, get_provider_embedding_class, RedBearModelFactory
from app.core.models.failover import (
    FailoverStats,
    attempt_config,
    is_initial_candidate,
    run_plan,
    run_plan_async,
)
from app.core.models.network_retry import network_retry
from app.core.usage_bridge import (
    report_usage_failure,
    report_usage_failure_async,
    report_usage_success,
    report_usage_success_async,
)
from app.models.models_model import ModelProvider

_USAGE_CAPABILITY = "embedding"

# 调用回调：收（本次候选客户端/模型，本次候选配置）——换渠道后模型名/api_key 均可能变
_ClientCall = Callable[[Any, RedBearModelConfig], Any]


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

    def _attempt_target(self, resolved: ResolvedModelConfig) -> tuple[Any, RedBearModelConfig]:
        """本次候选的（客户端/模型, 配置）：首候选沿用现实例，换渠道后按候选重建。"""
        if is_initial_candidate(self._config, resolved):
            return (self._client if self._is_volcano else self._model), self._config
        config = attempt_config(self._config, resolved)
        if config.provider.lower() == ModelProvider.VOLCANO:
            return self._create_volcano_client(config), config
        return self._create_model(config), config

    def _observed_call(self, operation: str, call: _ClientCall):
        started = time.perf_counter()
        stats = FailoverStats()
        plan = self._config.failover_plan
        try:
            if plan is None:
                @network_retry
                def _call():
                    return call(self._client if self._is_volcano else self._model, self._config)
                result = _call()
            else:
                outcome = run_plan(
                    plan,
                    invoke=lambda resolved: call(*self._attempt_target(resolved)),
                    stats=stats,
                )
                result = outcome.result
        except Exception as exc:
            attrib = stats.attribution_config(self._config)
            report_model_gateway_failure(attrib, operation, exc, started)
            report_usage_failure(
                attrib, _USAGE_CAPABILITY, operation, exc, started,
                attempts=stats.counted(),
            )
            raise
        attrib = stats.attribution_config(self._config)
        report_model_gateway_success(attrib, operation, started)
        report_usage_success(
            attrib, _USAGE_CAPABILITY, operation, started, result=result,
            attempts=stats.counted(), fallback=stats.switched,
        )
        return result

    async def _observed_async_call(self, operation: str, call: _ClientCall):
        started = time.perf_counter()
        stats = FailoverStats()
        plan = self._config.failover_plan
        try:
            if plan is None:
                @network_retry
                async def _call():
                    target = self._client if self._is_volcano else self._model
                    return await call(target, self._config)
                result = await _call()
            else:
                async def _invoke(resolved: ResolvedModelConfig) -> Any:
                    target, attempt_cfg = self._attempt_target(resolved)
                    return await call(target, attempt_cfg)

                outcome = await run_plan_async(plan, invoke=_invoke, stats=stats)
                result = outcome.result
        except Exception as exc:
            attrib = stats.attribution_config(self._config)
            await report_model_gateway_failure_async(attrib, operation, exc, started)
            await report_usage_failure_async(
                attrib, _USAGE_CAPABILITY, operation, exc, started,
                attempts=stats.counted(),
            )
            raise
        attrib = stats.attribution_config(self._config)
        await report_model_gateway_success_async(attrib, operation, started)
        await report_usage_success_async(
            attrib, _USAGE_CAPABILITY, operation, started, result=result,
            attempts=stats.counted(), fallback=stats.switched,
        )
        return result

    @staticmethod
    def _create_model(config: RedBearModelConfig) -> Embeddings:
        """根据配置创建 LangChain 模型"""
        embedding_class = get_provider_embedding_class(config.provider)
        provider = config.provider.lower()
        # Embedding models only need connection params, never LLM-specific ones
        # (e.g. enable_thinking, model_kwargs) — build params directly.
        if provider in [
            ModelProvider.OPENAI,
            ModelProvider.XINFERENCE,
            ModelProvider.GPUSTACK,
            ModelProvider.SPEEDBEAR,
        ]:
            import httpx
            # 连接超时跟随调用预算（上限 60s），短预算场景（如配置验证）快速失败
            timeout = httpx.Timeout(timeout=config.timeout, connect=min(config.timeout, 60.0))
            params = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout,
                "max_retries": config.max_retries,
                "chunk_size": settings.EMBEDDING_BATCH_SIZE,
            }
            if provider == ModelProvider.SPEEDBEAR:
                params["check_embedding_ctx_length"] = False
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
            contents = [{"type": "text", "text": text} for text in texts]

            def invoke(client, config):
                response = client.multimodal_embeddings.create(
                    model=config.model_name,
                    input=contents,
                    encoding_format="float"
                )
                return [response.data.embedding]

            return self._observed_call("embed_documents", invoke)
        return self._observed_call(
            "embed_documents", lambda client, config: client.embed_documents(texts)
        )

    def embed_query(self, text: str) -> List[float]:
        """单个文本向量化（LangChain 标准接口）"""
        if self._is_volcano:
            result = self.embed_documents([text])
            return result[0] if result else []
        return self._observed_call("embed_query", lambda client, config: client.embed_query(text))

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（异步）"""
        if self._is_volcano:
            return await asyncio.to_thread(self.embed_documents, texts)
        return await self._observed_async_call(
            "aembed_documents", lambda client, config: client.aembed_documents(texts)
        )

    async def aembed_query(self, text: str) -> List[float]:
        """单个文本向量化（异步）"""
        if self._is_volcano:
            result = await self.aembed_documents([text])
            return result[0] if result else []
        return await self._observed_async_call(
            "aembed_query", lambda client, config: client.aembed_query(text)
        )
    
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
        
        def invoke(client, config):
            response = client.multimodal_embeddings.create(
                model=config.model_name,
                input=contents,
                **kwargs
            )
            return [response.data.embedding]

        return self._observed_call("embed_multimodal", invoke)

    async def aembed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """异步多模态向量化"""
        # 火山引擎 SDK 暂不支持异步：同步调用移出事件循环（不得在 async 内直调同步阻塞）
        return await asyncio.to_thread(self.embed_multimodal, contents, **kwargs)
    
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
