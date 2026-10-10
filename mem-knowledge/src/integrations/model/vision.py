"""视觉壳（QWenCV，G4b）：图 → 文走 llm 族多模态消息（data URI），凭据不出 km。

旧 ``rag.models.vision.QWenCV`` 的壳化版本：只换底座（``RedBearLLM`` → 同步
``RedBearChatModel`` 壳），消息形态、prompt 文案与返回口径保持不变。纯 sync 站点
（celery ``parse_document`` 的图 → 文）使用。
"""

from __future__ import annotations

import base64

from .chat import RedBearChatModel, message_text, message_token_count
from .invoke_backend import RemoteInvokeRef
from .runtime import ModelInvokeRuntime


class QWenCV:
    """图 → 文：持同步 chat 壳（llm 族多模态消息，服务侧选路）。"""

    @classmethod
    def for_invoke_sync_ref(
        cls, ref: RemoteInvokeRef, *, pool: ModelInvokeRuntime, lang: str = "Chinese"
    ) -> QWenCV:
        """远端同步模式：与旧 ``QWenCV(config, client_pool=...)`` 同用法。"""

        instance = cls.__new__(cls)
        instance.lang = lang
        instance._model = RedBearChatModel.for_invoke_sync_ref(ref, pool=pool)
        return instance

    def describe(self, image: bytes) -> tuple[str, int]:
        prompt = (
            "请准确描述图片内容，并提取所有可见文字和数据。"
            if self.lang.lower() == "chinese"
            else "Describe the image accurately and extract all visible text and data."
        )
        return self.describe_with_prompt(image, prompt)

    def describe_with_prompt(
        self,
        image: bytes,
        prompt: str | None = None,
    ) -> tuple[str, int]:
        encoded = base64.b64encode(image).decode("ascii")
        media_type = "image/jpeg"
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        response = self._model.invoke(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "Describe the image."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                }
            ]
        )
        text = message_text(response)
        if not text:
            raise RuntimeError("Image model returned empty content")
        return text, message_token_count(response, text)


__all__ = ["QWenCV"]
