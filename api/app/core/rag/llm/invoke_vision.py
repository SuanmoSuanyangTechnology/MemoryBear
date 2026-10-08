"""chunk 管线视觉 / 转写模型接缝（G4a）：宿主只持配置引用，凭据与选路在模型服务。

管线消费面（``core/rag/chunk/pipeline/media.py``、``core/rag/app/picture.py``、
``core/rag/chunk/parser/image_vision.py``）只需要旧 ``QWenCV`` / ``QWenSeq2txt`` 的四个方法
（``describe`` / ``describe_with_prompt`` / ``chat(video)`` / ``transcription``）与 ``lang``
属性；本模块给出远端等价，替代把明文凭据装进旧壳：

- 图像 / 视频理解走 **llm 族多模态消息**（``messages`` 原始 dict 透传、内容块原样过线）：
  图片以 data URI 内联；视频优先远端 URL，本地字节 ≤1MB 才内联兜底，超限且无 URL 响亮拒止；
- 音频转写走 **asr 族**：契约只收公网 URL（服务侧轮询至完成），本地路径参数不再出站。

全部为同步阻塞面（``RedBearChatModel.for_invoke_sync_ref`` / ``call_asr_sync``）：消费方是
同步 chunk 管线（celery ``parse_document``；HTTP 侧经 ``asyncio.to_thread``），且图像描述在
``figure_parser.shared_executor``（10 线程）并发调用——宿主同步 invoke 池为进程级单例，
httpx 同步客户端可跨线程复用。
"""

from __future__ import annotations

import base64
import io
import re
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import magic

from app.core.models.chat import RedBearChatModel
from app.core.rag.common.token_utils import num_tokens_from_string
from app.core.rag.prompts.generator import vision_llm_describe_prompt
from app.integrations.model.invoke_backend import (
    MAX_INLINE_MEDIA_BYTES,
    RemoteInvokeRef,
    call_asr_sync,
    ref_from_model_info,
)

if TYPE_CHECKING:
    from app.schemas.model_schema import ModelInfo

#: 文件类别 → 配置槽：audio→audio2text / video→video2text / 其余（含文档配图）→image2text
_AUDIO_EXT_PATTERN = re.compile(
    r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|realaudio|vqf|oggvorbis|ape?)$",
    re.IGNORECASE,
)
_VIDEO_EXT_PATTERN = re.compile(
    r"\.(mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$",
    re.IGNORECASE,
)

#: 默认描述提示与视频时间戳转录提示：文案与旧壳（``cv_model`` / ``QWenCV._process_video``）
#: 逐字对齐，保证迁移不改变模型行为。
_DESCRIBE_PROMPT_ZH = (
    "请用中文详细描述一下图中的内容，比如时间，地点，人物，事情，人物心情等，如果有数据请提取出数据。"
)
_DESCRIBE_PROMPT_EN = (
    "Please describe the content of this picture, like where, when, who, what happen. "
    "If it has number data, please extract them out."
)
_VIDEO_PROMPT_ZH = (
    "你是一名专业的视频转录助手，能够将视频文件的内容转写为文本，并**精确标记每句话或每个段落对应的时间戳**（开始时间-结束时间）。\n"
    "**任务要求**：\n"
    "1.输入是MP4等视频文件,解析带时间戳的文本。\n"
    "2.时间戳格式为 `[HH:MM:SS.mmm]`（毫秒可选），例如 `[00:01:23.456]`。\n"
    "3.时间戳需尽可能贴近实际视频的起止时间，误差不超过1秒。\n"
    "4.如果无法确定具体时间，请根据上下文合理估算。\n"
    "5.最后总结:这段视频的内容是什么?,并用恰当的句子总结这个视频。\n\n"
    "**示例输出**：\n"
    "[00:00:00.000] 今天天气真好，\n"
    "[00:00:02.500] 我们一起去公园散步吧。\n"
    "[00:00:05.800] 公园里的花开得非常漂亮。\n"
    "这段视频的内容是关于如何在CREAMS系统中进行楼宇管理集合的编辑或删除操作。视频演示了 ..."
)
_VIDEO_PROMPT_EN = (
    "You are a professional video transcription assistant, capable of transcribing the content "
    "of video files into text and **precisely marking the timestamp (start time-end time) "
    "corresponding to each sentence or paragraph**.\n"
    "**Task requirements**:\n"
    "1. Input is MP4 or other video files, and parse the text with timestamps.\n"
    "2. The timestamp format is `[HH:MM:SS.mmm]` (milliseconds are optional), for example `[00:01:23.456]`.\n"
    "3. The timestamp should be as close as possible to the actual start and end time of the video, with an error not exceeding 1 second.\n"
    "4. If the specific time cannot be determined, please make a reasonable estimation based on the context.\n"
    "5. Final summary: What is the content of this video? Summarize this video in an appropriate sentence.\n\n"
    "**Example output**:\n"
    "[00:00:00.000] The weather is really nice today, [00:00:02.500] let's go for a walk in the park together.\n"
    "[00:00:05.800] The flowers in the park are blooming beautifully.\n"
    "The content of this video is about how to edit or delete building management collections in the CREAMS system. The video demonstrates .."
)


def vision_media_kind(file_name: str) -> str:
    """文件类别：``"audio"`` / ``"video"`` / ``"image"``（含文档，图文统一走 image2text 槽）。"""

    name = file_name or ""
    if _AUDIO_EXT_PATTERN.search(name):
        return "audio"
    if _VIDEO_EXT_PATTERN.search(name):
        return "video"
    return "image"


def vision_slot_id(knowledge: Any, kind: str) -> uuid.UUID | None:
    """文件类别 → 知识库上的模型配置槽 id（缺失返回 ``None``，由调用方响亮拒止）。"""

    if kind == "audio":
        return knowledge.audio2text_id
    if kind == "video":
        return knowledge.video2text_id
    return knowledge.image2text_id


def externally_reachable_media_url(url: str | None) -> str | None:
    """http(s) 才可交给远端取媒体；本地存储返回相对路径，按无 URL 处理。"""

    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    return None


def build_chunk_vision_model(
    view: "ModelInfo",
    *,
    kind: str,
    lang: str = "Chinese",
    media_url: str | None = None,
) -> InvokeVisionModel | InvokeTranscriptionModel:
    """按文件类别构造 chunk 管线模型：audio → asr 转写；video / image → llm 多模态理解。

    ``media_url`` 仅音频 / 视频消费（外部可达的签名 URL）；非 http(s) 视为无 URL，
    由调用（``transcription`` / ``chat``）在需要时响亮报错。
    """

    ref = ref_from_model_info(view)
    if kind == "audio":
        return InvokeTranscriptionModel(ref, file_url=media_url, lang=lang)
    return InvokeVisionModel(ref, lang=lang, media_url=media_url)


class InvokeVisionModel:
    """图像 / 视频理解（llm 族多模态消息）的同步接缝，替代 ``QWenCV`` 的管线消费面。"""

    def __init__(
        self,
        ref: RemoteInvokeRef,
        *,
        lang: str = "Chinese",
        media_url: str | None = None,
    ):
        self.lang = lang
        self._chat = RedBearChatModel.for_invoke_sync_ref(ref)
        self._media_url = externally_reachable_media_url(media_url)

    def describe(self, image: Any) -> tuple[str, int]:
        prompt = (
            _DESCRIBE_PROMPT_ZH
            if str(self.lang).lower() == "chinese"
            else _DESCRIBE_PROMPT_EN
        )
        return self._describe_with(image, prompt)

    def describe_with_prompt(self, image: Any, prompt: str | None = None) -> tuple[str, int]:
        return self._describe_with(image, prompt or vision_llm_describe_prompt(lang=self.lang))

    def chat(
        self,
        system: str,
        history: list,
        gen_conf: dict,
        images: Any = None,
        video_bytes: bytes | None = None,
        filename: str = "",
        **kwargs: Any,
    ) -> tuple[str, int]:
        """管线旧协议入口：本壳只支持视频理解（``video_bytes``）。"""

        if not video_bytes:
            raise RuntimeError(
                "chat 仅支持视频理解（需传 video_bytes）；图像描述请用 describe/describe_with_prompt"
            )
        prompt = (
            _VIDEO_PROMPT_ZH
            if str(self.lang).lower() == "chinese"
            else _VIDEO_PROMPT_EN
        )
        messages = [
            {
                "role": "user",
                "content": [
                    _video_content_block(video_bytes, filename, self._media_url),
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        return self._invoke(messages)

    def _describe_with(self, image: Any, prompt: str) -> tuple[str, int]:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                ],
            }
        ]
        return self._invoke(messages)

    def _invoke(self, messages: list[dict[str, Any]]) -> tuple[str, int]:
        message = self._chat.invoke(messages)
        text = _message_text(message)
        return text.strip(), _message_token_count(message, text)


class InvokeTranscriptionModel:
    """音频转写（asr 族）的同步接缝，替代 ``QWenSeq2txt.transcription``。

    构造时给文件的外部可达 URL（asr 族只收公网 URL）；``transcription`` 收到的本地
    路径不参与出站——出站由服务侧按 URL 取字节。
    """

    def __init__(
        self,
        ref: RemoteInvokeRef,
        *,
        file_url: str | None,
        lang: str = "Chinese",
    ):
        self.lang = lang
        self._ref = ref
        self._file_url = externally_reachable_media_url(file_url)

    def transcription(self, audio_path: str) -> tuple[str, int]:
        if not self._file_url:
            raise RuntimeError(
                "音频转写需要可外部访问的文件 URL（asr 族只收公网 URL，当前存储未返回）"
            )
        text = call_asr_sync(self._ref, file_url=self._file_url)
        return text.strip(), num_tokens_from_string(text)


def _image_data_url(image: Any) -> str:
    """bytes / BytesIO 原样编码；PIL Image 兜底转 JPEG（失败转 PNG），与旧壳分工一致。"""

    if isinstance(image, (bytes, bytearray, memoryview)):
        data = bytes(image)
        return _data_url(data, _image_mime(data, declared=None))
    if isinstance(image, io.BytesIO):
        data = image.getvalue()
        return _data_url(data, _image_mime(data, declared=None))
    with io.BytesIO() as buffer:
        try:
            image.save(buffer, format="JPEG")
            mime = "image/jpeg"
        except Exception:
            buffer.seek(0)
            buffer.truncate()
            image.save(buffer, format="PNG")
            mime = "image/png"
        return _data_url(buffer.getvalue(), mime)


def _video_content_block(video_bytes: bytes, filename: str, media_url: str | None) -> dict[str, Any]:
    """视频内容块：优先远端 URL；本地字节 ≤1MB（契约内联上限）才内联兜底，超限响亮拒止。"""

    if media_url:
        return {"type": "video_url", "video_url": {"url": media_url}}
    if len(video_bytes) <= MAX_INLINE_MEDIA_BYTES:
        mime = _video_mime(video_bytes, filename)
        return {"type": "video_url", "video_url": {"url": _data_url(video_bytes, mime)}}
    raise RuntimeError(
        "视频理解需要可外部访问的文件 URL"
        f"（文件 {len(video_bytes)} 字节，超过 {MAX_INLINE_MEDIA_BYTES} 内联上限且存储未返回 http(s) 地址）"
    )


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _image_mime(data: bytes, declared: str | None) -> str:
    detected = _magic_mime(data)
    if detected.startswith("image/"):
        return detected
    if declared:
        return declared
    return "image/png"


def _video_mime(data: bytes, filename: str) -> str:
    detected = _magic_mime(data)
    if detected.startswith("video/"):
        return detected
    if "." in (filename or ""):
        guessed = f"video/{filename.rsplit('.', 1)[-1].lower()}"
        if guessed != "video/":
            return guessed
    return "video/mp4"


def _magic_mime(data: bytes) -> str:
    try:
        detected = magic.from_buffer(data, mime=True)
    except Exception:
        return ""
    return detected.split(";", 1)[0].strip() if isinstance(detected, str) else ""


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


__all__ = [
    "InvokeTranscriptionModel",
    "InvokeVisionModel",
    "build_chunk_vision_model",
    "externally_reachable_media_url",
    "vision_media_kind",
    "vision_slot_id",
]
