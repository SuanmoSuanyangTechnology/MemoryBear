"""Provider-specific parameter and SDK adapters."""

from .dashscope_multimodal_embedding import DashScopeMultimodalEmbeddingAdapter
from .dashscope_multimodal_rerank import DashScopeMultimodalRerankAdapter

__all__ = [
    "DashScopeMultimodalEmbeddingAdapter",
    "DashScopeMultimodalRerankAdapter",
]
