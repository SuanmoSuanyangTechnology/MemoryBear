"""Lazy runtime exports loaded only when the requested class is used."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "RedBearEmbeddings": (".embedding", "RedBearEmbeddings"),
    "RedBearMultimodalEmbeddings": (".embedding", "RedBearMultimodalEmbeddings"),
    "RedBearImageGenerator": (".generation", "RedBearImageGenerator"),
    "RedBearVideoGenerator": (".generation", "RedBearVideoGenerator"),
    "RedBearLLM": (".llm", "RedBearLLM"),
    "StructResponse": (".llm", "StructResponse"),
    "RedBearRerank": (".rerank", "RedBearRerank"),
}

__all__ = [
    "RedBearEmbeddings",
    "RedBearImageGenerator",
    "RedBearLLM",
    "RedBearMultimodalEmbeddings",
    "RedBearRerank",
    "RedBearVideoGenerator",
    "StructResponse",
]


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    return getattr(import_module(module_name, __name__), attribute_name)
