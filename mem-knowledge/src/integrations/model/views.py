"""非解密视图的能力判定（km 本地单点）。

镜像包内 ``is_qwen3_vl_embedding`` / ``is_qwen3_vl_reranker``（dashscope provider +
type + ``model_name`` + IMAGE 输入模态逐字段对齐；provider 归一防 volcano 等误宽）。
输入为**视图**对象之一：``ModelConfigSnapshot``（读 ``name``）或 km 侧
``ModelRuntimeSnapshot``（读 ``model_name``）——本模块纯 duck-typing，保持叶子
依赖（integrations 不 import rag）。
"""

from __future__ import annotations

from typing import Any

from redbear_model.contracts import Modality, ModelProfile, ModelProvider, ModelType


def _view_name(view: Any) -> str:
    """调用锚点名：契约快照 ``name`` / 运行快照 ``model_name`` 双拼写。"""

    return str(getattr(view, "model_name", None) or getattr(view, "name", None) or "")


def _view_provider(view: Any) -> ModelProvider | None:
    value = getattr(view, "provider", None)
    if isinstance(value, ModelProvider):
        return value
    try:
        return ModelProvider(str(value))
    except ValueError:
        return None


def _view_profile(view: Any) -> ModelProfile | None:
    profile = getattr(view, "profile", None)
    return profile if isinstance(profile, ModelProfile) else None


def _is_qwen3_vl(
    view: Any, *, model_type: ModelType, model_name: str
) -> bool:
    profile = _view_profile(view)
    return (
        profile is not None
        and _view_provider(view) is ModelProvider.DASHSCOPE
        and profile.type is model_type
        and _view_name(view) == model_name
        and Modality.IMAGE in profile.input_modalities
    )


def is_qwen3_vl_embedding_view(view: Any) -> bool:
    """视图判定：qwen3-vl 单向量融合 embedding（结构化入参路径）。"""

    return _is_qwen3_vl(view, model_type=ModelType.EMBEDDING, model_name="qwen3-vl-embedding")


def is_qwen3_vl_rerank_view(view: Any) -> bool:
    """视图判定：qwen3-vl 原生多模态 rerank。"""

    return _is_qwen3_vl(view, model_type=ModelType.RERANK, model_name="qwen3-vl-rerank")


__all__ = ["is_qwen3_vl_embedding_view", "is_qwen3_vl_rerank_view"]
