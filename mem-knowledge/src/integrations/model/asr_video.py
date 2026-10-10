"""音频 / 视频壳（G4b）：单次 invoke，任务轮询全部收敛在服务侧。

旧 ``rag.models.media_runtime`` 两个 ChunkModel 的壳化版本（接口形状不变，chunk 管线
零改动消费）：

- 音频：**asr 族**同步直调——服务侧 submit→poll(1s/600s)→fetch，km 零协议面；只收
  公网 URL（本地文件先落存储换 URL，构造站点已保证）；
- 视频：无独立族——经 llm 壳 ``video_url`` 内容块（qwen-vl 类模型原生支持）。

两者都是同步阻塞面（celery ``parse_document``）：壳内持 ``for_invoke_sync_ref``。
"""

from __future__ import annotations

from typing import Any

from ...rag.chunk.prompts import video_transcription_prompt
from ...rag.chunk.token_utils import num_tokens_from_string
from .chat import RedBearChatModel, message_text, message_token_count
from .invoke_backend import RemoteInvokeRef, call_asr_sync
from .runtime import ModelInvokeRuntime


class AudioTranscriptionChunkModel:
    """音频转写壳：``transcription(path) -> (text, tokens)``（``path`` 仅为接口对齐）。"""

    @classmethod
    def for_invoke_sync_ref(
        cls, ref: RemoteInvokeRef, *, pool: ModelInvokeRuntime, file_url: str
    ) -> AudioTranscriptionChunkModel:
        """远端同步模式：``file_url`` 须外部可达（asr 族只收公网 URL）。"""

        instance = cls.__new__(cls)
        instance._remote = ref
        instance._pool = pool
        instance._file_url = file_url
        return instance

    def transcription(self, _audio_path: str) -> tuple[str, int]:
        """同步转写：服务侧阻塞至任务完成；本地路径参数不出站。"""

        text = call_asr_sync(self._pool, self._remote, file_url=self._file_url)
        return text, num_tokens_from_string(text)


class VideoUnderstandingChunkModel:
    """视频理解壳：``chat(...) -> (text, tokens)``，经 llm 族 ``video_url`` 内容块。"""

    @classmethod
    def for_invoke_sync_ref(
        cls,
        ref: RemoteInvokeRef,
        *,
        pool: ModelInvokeRuntime,
        video_url: str,
        lang: str = "Chinese",
    ) -> VideoUnderstandingChunkModel:
        """远端同步模式：``video_url`` 须外部可达（服务侧按 URL 取字节）。"""

        instance = cls.__new__(cls)
        instance.lang = lang
        instance._video_url = video_url
        instance._model = RedBearChatModel.for_invoke_sync_ref(ref, pool=pool)
        return instance

    def chat(
        self,
        system: str,
        history: list,
        gen_conf: dict,
        *,
        video_bytes: bytes | None = None,
        filename: str = "",
        **kwargs: Any,
    ) -> tuple[str, int]:
        """视频 → 带时间戳转录文本（``video_bytes`` 等参数仅为接口对齐）。"""

        del system, history, gen_conf, video_bytes, filename, kwargs
        response = self._model.invoke(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": video_transcription_prompt(self.lang)},
                        {"type": "video_url", "video_url": {"url": self._video_url}},
                    ],
                }
            ]
        )
        text = message_text(response)
        return text, message_token_count(response, text)


__all__ = ["AudioTranscriptionChunkModel", "VideoUnderstandingChunkModel"]
