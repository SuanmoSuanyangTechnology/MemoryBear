from __future__ import annotations
from typing import Any, Iterator, AsyncIterator, List, Optional
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseLLM
from langchain_core.outputs import LLMResult, GenerationChunk

from app.core.models import RedBearModelConfig, RedBearModelFactory, get_provider_llm_class
from app.models.models_model import ModelType


class RedBearLLM(BaseLLM):
    """
    RedBear LLM Model Wrapper
    
    This wrapper provides a unified interface to access different LLM providers,
    while maintaining all LangChain functionality, including streaming output.
    
    Features:
    - Support for multiple LLM providers (OpenAI, Qwen, Ollama, etc.)
    - Full streaming output support
    - Elegant error handling and fallback mechanism
    - Automatic proxying of all underlying model methods and attributes
    """

    def __init__(self, config: RedBearModelConfig, type: ModelType = ModelType.LLM):
        """Initialize RedBear LLM wrapper
        
        Args:
            config: Model configuration
            type: Model type (LLM or CHAT)
        """
        super().__init__()
        self._config = config
        self._model = self._create_model(config, type)

    @property
    def _llm_type(self) -> str:
        """Return LLM type identifier"""
        return getattr(self._model, '_llm_type', 'redbear_llm')

    # ==================== Core Methods (Required by BaseLLM) ====================
    
    def _generate(
        self,
        prompts: List[str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> LLMResult:
        """Synchronous text generation (required by BaseLLM)"""
        return self._model._generate(prompts, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        prompts: List[str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> LLMResult:
        """Asynchronous text generation (required by BaseLLM)"""
        return await self._model._agenerate(prompts, stop=stop, run_manager=run_manager, **kwargs)

    # ==================== Advanced Methods (Support Message Lists) ====================
    
    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        """Synchronous model invocation
        
        Supports various input formats including strings and message lists.
        Directly delegates to the underlying model to avoid BaseLLM's string conversion.
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            **kwargs: Additional arguments
            
        Returns:
            Model response
        """
        try:
            return self._model.invoke(input, config=config, **kwargs)
        except AttributeError as e:
            if 'invoke' in str(e):
                # Underlying model doesn't support invoke, fallback to parent implementation
                return super().invoke(input, config=config, **kwargs)
            raise
        except Exception:
            # Other exceptions are raised directly
            raise

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        """Asynchronous model invocation
        
        Supports various input formats including strings and message lists.
        Directly delegates to the underlying model to avoid BaseLLM's string conversion.
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            **kwargs: Additional arguments
            
        Returns:
            Model response
        """
        try:
            return await self._model.ainvoke(input, config=config, **kwargs)
        except AttributeError as e:
            if 'ainvoke' in str(e):
                # Underlying model doesn't support ainvoke, fallback to parent implementation
                return await super().ainvoke(input, config=config, **kwargs)
            raise
        except Exception:
            # Other exceptions are raised directly
            raise

    # ==================== Streaming Methods (Critical) ====================
    
    def stream(
        self,
        input: Any,
        config: Optional[dict] = None,
        *,
        stop: Optional[List[str]] = None,
        **kwargs: Any
    ) -> Iterator[GenerationChunk]:
        """Synchronous streaming model invocation
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            stop: List of stop words
            **kwargs: Additional arguments
            
        Yields:
            GenerationChunk: Generated text chunks
        """
        try:
            yield from self._model.stream(input, config=config, stop=stop, **kwargs)
        except AttributeError as e:
            if 'stream' in str(e):
                # Underlying model doesn't support stream, fallback to parent implementation
                yield from super().stream(input, config=config, stop=stop, **kwargs)
            else:
                raise
        except Exception:
            raise
    
    async def astream(
        self,
        input: Any,
        config: Optional[dict] = None,
        *,
        stop: Optional[List[str]] = None,
        **kwargs: Any
    ) -> AsyncIterator[GenerationChunk]:
        """Asynchronous streaming model invocation
        
        This is the core method for streaming output. It directly proxies to the
        underlying model's astream method, maintaining generator characteristics
        to ensure each chunk is delivered in real-time.
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            stop: List of stop words
            **kwargs: Additional arguments
            
        Yields:
            GenerationChunk: Generated text chunks
        """
        try:
            async for chunk in self._model.astream(input, config=config, stop=stop, **kwargs):
                yield chunk
        except AttributeError as e:
            if 'astream' in str(e):
                # Underlying model doesn't support astream, fallback to parent implementation
                async for chunk in super().astream(input, config=config, stop=stop, **kwargs):
                    yield chunk
            else:
                raise
        except Exception:
            raise

    # ==================== Dynamic Proxy ====================
    
    def __getattr__(self, name: str) -> Any:
        """Dynamic proxy: delegate undefined attributes and method calls to internal model
        
        This method allows RedBearLLM to transparently access all attributes and methods
        of the underlying model without explicitly defining each one.
        
        Args:
            name: Attribute or method name
            
        Returns:
            Attribute value or method
            
        Raises:
            AttributeError: If attribute doesn't exist
        """
        # Avoid recursion: raise error directly for special attributes
        if name in ('__isabstractmethod__', '__dict__', '__class__', '_model', '_config'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        
        # Try to get attribute from internal model
        try:
            attr = object.__getattribute__(self._model, name)
            
            # If it's callable (a method)
            if callable(attr):
                # Streaming methods are returned directly to maintain generator characteristics
                # Note: Although we've explicitly implemented stream/astream,
                # this is kept to handle internal methods like _stream/_astream
                if name in ('_stream', '_astream'):
                    return attr
                
                # Wrap other methods for easier debugging and error handling
                def method_wrapper(*args, **kwargs):
                    try:
                        return attr(*args, **kwargs)
                    except Exception:
                        # Can add logging or error handling here
                        raise
                
                # Preserve method metadata
                method_wrapper.__name__ = name
                method_wrapper.__doc__ = getattr(attr, '__doc__', f"Delegated method: {name}")
                return method_wrapper
            
            # If it's a regular attribute, return directly
            return attr
            
        except AttributeError:
            # Internal model doesn't have this attribute either
            pass
        
        # Check if there's a fallback method
        fallback_name = f'_fallback_{name}'
        try:
            return object.__getattribute__(self, fallback_name)
        except AttributeError:
            pass
        
        # Nothing found, raise error
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'. "
            f"The underlying model '{type(self._model).__name__}' also doesn't have this attribute."
        )

    # ==================== Helper Methods ====================
    
    def _create_model(self, config: RedBearModelConfig, type: ModelType) -> BaseLLM:
        """Create internal model instance
        
        Args:
            config: Model configuration
            type: Model type
            
        Returns:
            Created model instance
        """
        llm_class = get_provider_llm_class(config, type)
        model_params = RedBearModelFactory.get_model_params(config)
        return llm_class(**model_params)
    
    def get_config(self) -> RedBearModelConfig:
        """Get model configuration
        
        Returns:
            Model configuration object
        """
        return self._config
    
    def get_underlying_model(self) -> BaseLLM:
        """Get underlying model instance
        
        Returns:
            Underlying model instance
        """
        return self._model
    
    def __repr__(self) -> str:
        """Return string representation of the object"""
        return (
            f"RedBearLLM("
            f"provider={self._config.provider}, "
            f"model={self._config.model_name}, "
            f"type={type(self._model).__name__}"
            f")"
        )