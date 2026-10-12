"""Lazy runtime exports loaded only when the requested class is used."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "RedBearVideoUnderstanding": (".video_understanding", "RedBearVideoUnderstanding"),
    "RedBearAudioTranscriber": (".audio", "RedBearAudioTranscriber"),
    "RedBearEmbeddings": (".embedding", "RedBearEmbeddings"),
    "RedBearMultimodalEmbeddings": (".embedding", "RedBearMultimodalEmbeddings"),
    "RedBearImageGenerator": (".generation", "RedBearImageGenerator"),
    "RedBearVideoGenerator": (".generation", "RedBearVideoGenerator"),
    "RedBearLLM": (".llm", "RedBearLLM"),
    "StructResponse": (".llm", "StructResponse"),
    "RedBearRerank": (".rerank", "RedBearRerank"),
    "normalize_runtime_flags": (".flags", "normalize_runtime_flags"),
    "AsyncInvokeTransport": (".remote", "AsyncInvokeTransport"),
    "SyncInvokeTransport": (".remote", "SyncInvokeTransport"),
    "InvokeChunkFrame": (".remote", "InvokeChunkFrame"),
    "InvokeDoneFrame": (".remote", "InvokeDoneFrame"),
    "InvokeFrame": (".remote", "InvokeFrame"),
    "InvokeRequest": (".remote", "InvokeRequest"),
    "InvokeResultFrame": (".remote", "InvokeResultFrame"),
    "InvokeTarget": (".remote", "InvokeTarget"),
    "InvokeTimeouts": (".remote", "InvokeTimeouts"),
    "InvokeUsageFrame": (".remote", "InvokeUsageFrame"),
    "RemoteRedBearChatModel": (".remote", "RemoteRedBearChatModel"),
    "INVOKE_PATH": (".remote", "INVOKE_PATH"),
    "messages_from_wire": (".remote", "messages_from_wire"),
}

__all__ = [
    "INVOKE_PATH",
    "AsyncInvokeTransport",
    "InvokeChunkFrame",
    "InvokeDoneFrame",
    "InvokeFrame",
    "InvokeRequest",
    "InvokeResultFrame",
    "InvokeTarget",
    "InvokeTimeouts",
    "InvokeUsageFrame",
    "RedBearAudioTranscriber",
    "RedBearEmbeddings",
    "RedBearImageGenerator",
    "RedBearLLM",
    "RedBearMultimodalEmbeddings",
    "RedBearRerank",
    "RedBearVideoGenerator",
    "RedBearVideoUnderstanding",
    "RemoteRedBearChatModel",
    "StructResponse",
    "SyncInvokeTransport",
    "messages_from_wire",
    "normalize_runtime_flags",
]


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    return getattr(import_module(module_name, __name__), attribute_name)
