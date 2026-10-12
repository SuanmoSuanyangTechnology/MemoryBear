"""KB-graph LEGACY 管道模型接缝（G5）：宿主只持配置引用，凭据与选路在模型服务。

LEGACY 图检索与图重建的消费方按旧 ``Base`` / ``OpenAIEmbed`` 协议消费模型对象
（``graphrag/search.py`` 的 ``KGSearch._chat``、``graphrag/general/extractor.py`` 及其子类、
``prompts/generator.py`` 的 ``qa_proposal`` / ``graph_entity_types``、``graphrag/utils.py`` 的
``graph_node_to_chunk`` / ``graph_edge_to_chunk``、``nlp/search.py`` 的 ``Dealer``）：

- ``chat(system, history, gen_conf=None, **kw) -> (text, tokens)``：失败不抛出，返回
  ``"**ERROR**: <原因>"`` 串保住调用方 fail-soft（``Extractor`` 自行重试三次、``qa_proposal``
  转为空列表、``KGSearch`` 抛错前有缓存检查点）；
- ``encode(texts) -> (vectors, tokens)`` / ``encode_queries(text) -> (vector, tokens)``：
  返回普通 ``list``（旧 ``np.array`` 消费方 ``len`` / ``np.array()`` / 下标取值对 list 等价）。

远端等价只带「哪个配置、代表哪个租户」（设计 §2.2）。全部为同步阻塞面
（``RedBearChatModel.for_invoke_sync_ref``）：消费方是 celery / 线程池 / ``trio.to_thread``
的同步现场，宿主同步 invoke 池为进程级单例，可跨线程复用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core.models.chat import RedBearChatModel
from app.core.models.embedding import RedBearEmbeddings
from app.core.rag.common.token_utils import num_tokens_from_string
from app.integrations.model.invoke_backend import RemoteInvokeRef

#: 与旧壳（``chat_model.ERROR_PREFIX``）同字面量：消费方按子串检查失败信号
_ERROR_PREFIX = "**ERROR**"


class InvokeLegacyChat:
    """旧 ``Base.chat`` 协议的远端等价（同步阻塞面）。

    ``model_name`` 对消费方是缓存键（``get_llm_cache`` / ``set_llm_cache``），照快照透传；
    ``max_length`` 等旧壳缺失属性消费方本就按 ``getattr`` 缺省兜底，不在此补齐。
    """

    def __init__(self, ref: RemoteInvokeRef, *, model_name: str):
        self.model_name = model_name
        self._chat = RedBearChatModel.for_invoke_sync_ref(ref)

    def chat(
        self,
        system: str,
        history: list,
        gen_conf: dict | None = None,
        **kwargs: Any,
    ) -> tuple[str, int]:
        messages: list[dict[str, Any]] = []
        if system and history and not _starts_with_system(history):
            messages.append({"role": "system", "content": system})
        messages.extend(history or [])

        params: dict[str, Any] = dict(gen_conf or {})
        params.update(kwargs)

        try:
            message = self._chat.invoke(messages, **params)
        except Exception as exc:  # noqa: BLE001 —— 旧协议约定：失败返回 ERROR 串而非抛出
            return f"{_ERROR_PREFIX}: {exc}", 0
        text = _message_text(message).strip()
        return text, _message_token_count(message, text)


class InvokeLegacyEmbed:
    """旧 ``OpenAIEmbed`` 协议的远端等价（同步阻塞面）。

    ``encode`` 返回与入参等长的普通 list；空白条目在宿主侧即刻响亮拒止（服务侧拒收空白，
    静默给零向量会污染图节点向量维度语义——旧壳同位置也是抛错路径）。
    """

    def __init__(self, ref: RemoteInvokeRef, *, model_name: str):
        self.model_name = model_name
        self._embeddings = RedBearEmbeddings.for_invoke(ref)

    def encode(self, texts: Sequence[str]) -> tuple[list[list[float]], int]:
        vectors = self._embeddings.embed_documents(list(texts))
        blank = next((i for i, vector in enumerate(vectors) if not vector), None)
        if blank is not None:
            raise ValueError(
                f"embedding 返回空向量（下标 {blank}）：空白文本不上线，请过滤后重试"
            )
        return vectors, 0  # type: ignore[return-value]

    def encode_queries(self, text: str) -> tuple[list[float], int]:
        vector = self._embeddings.embed_query(text)
        if not vector:
            raise ValueError("embedding 返回空查询向量：空白文本不上线")
        return vector, 0


def _starts_with_system(history: Sequence[Any]) -> bool:
    first = history[0]
    if isinstance(first, Mapping):
        return first.get("role") == "system"
    return getattr(first, "type", None) == "system"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


def _message_token_count(message: Any, text: str) -> int:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, Mapping):
        total = usage.get("total_tokens")
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            return total
    return num_tokens_from_string(text)


__all__ = ["InvokeLegacyChat", "InvokeLegacyEmbed"]
