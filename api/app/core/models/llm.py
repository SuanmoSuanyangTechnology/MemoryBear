from __future__ import annotations
from typing import Any, Dict, List, Optional
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseLLM
from langchain_core.outputs import LLMResult

from app.core.models import RedBearModelConfig, RedBearModelFactory, get_provider_llm_class
from app.models.models_model import ModelType


class RedBearLLM(BaseLLM):
    """
    RedBear LLM 模型包装器 - 完全动态代理实现
    
    这个包装器自动将所有方法调用委托给内部模型，
    同时提供优雅的回退机制和错误处理。
    """

    def __init__(self, config: RedBearModelConfig, type: ModelType=ModelType.LLM):
        self._model = self._create_model(config, type)
        self._config = config

    @property
    def _llm_type(self) -> str:
        """返回LLM类型标识符"""
        return self._model._llm_type

    def _generate(
        self,
        prompts: List[str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> LLMResult:
        """同步生成文本"""
        return self._model._generate(prompts, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        prompts: List[str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> LLMResult:
        """异步生成文本"""
        return await self._model._agenerate(prompts, stop=stop, run_manager=run_manager, **kwargs)

    # 关键：覆盖 invoke/ainvoke，直接委托到底层模型，避免 BaseLLM 的字符串化行为
    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        """直接调用底层模型以支持 ChatPrompt 和消息列表。"""
        try:
            return self._model.invoke(input, config=config, **kwargs)
        except AttributeError as e:
            # 只在属性错误时回退（说明底层模型不支持该方法）
            if 'invoke' in str(e):
                return super().invoke(input, config=config, **kwargs)
            # 其他 AttributeError 直接抛出
            raise
        except Exception:
            # 其他所有异常（包括 ValidationException）直接抛出，不回退
            raise

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        """异步直接调用底层模型以支持 ChatPrompt 和消息列表。"""
        try:
            return await self._model.ainvoke(input, config=config, **kwargs)
        except AttributeError as e:
            # 只在属性错误时回退（说明底层模型不支持该方法）
            if 'ainvoke' in str(e):
                return await super().ainvoke(input, config=config, **kwargs)
            # 其他 AttributeError 直接抛出
            raise
        except Exception:
            # 其他所有异常（包括 ValidationException）直接抛出，不回退
            raise

    def __getattr__(self, name):
        """
        动态代理：将所有未定义的属性和方法调用委托给内部模型
        
        这是最优雅的包装器实现方式，完全避免了方法重复定义
        """
        # 处理特殊属性以避免递归
        if name in ('__isabstractmethod__', '__dict__', '__class__'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
            
        # 检查内部模型是否有该属性（使用安全的方式避免递归）
        try:
            # 使用 object.__getattribute__ 来安全地检查内部模型的属性
            attr = object.__getattribute__(self._model, name)
            
            # 如果是方法，返回一个包装器来处理调用
            if callable(attr):
                # 流式方法直接返回，不包装（保持生成器特性）
                if name in ('_stream', '_astream', 'stream', 'astream'):
                    return attr
                
                # 非流式方法使用包装器处理异常
                def method_wrapper(*args, **kwargs):
                    return attr(*args, **kwargs)
                
                # 保持方法的元信息
                method_wrapper.__name__ = name
                method_wrapper.__doc__ = getattr(attr, '__doc__', f"Delegated method: {name}")
                return method_wrapper
            
            # 如果是普通属性，直接返回
            return attr
            
        except AttributeError:
            # 内部模型没有该属性，尝试回退实现
            pass
        
        # 检查是否有回退方法（使用安全的方式避免递归）
        fallback_name = f'_fallback_{name}'
        try:
            fallback_method = object.__getattribute__(self, fallback_name)
            return fallback_method
        except AttributeError:
            # 没有回退方法，抛出适当的错误
            pass
        
        # 如果都没有，抛出适当的错误
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def _create_model(self, config: RedBearModelConfig, type: ModelType) -> BaseLLM:
        """创建内部模型实例"""
        llm_class = get_provider_llm_class(config, type)
        model_params = RedBearModelFactory.get_model_params(config)
        return llm_class(**model_params)


   