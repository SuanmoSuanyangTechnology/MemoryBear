import base64
import io
import json
import os
import re
from abc import ABC

import requests
from openai import OpenAI
from openai.lib.azure import AzureOpenAI

from app.core.rag.common.token_utils import num_tokens_from_string


class Base(ABC):
    def __init__(self, key, model_name, **kwargs):
        """
        Abstract base class constructor.
        Parameters are not stored; initialization is left to subclasses.
        """
        pass

    def transcription(self, audio_path, **kwargs):
        audio_file = open(audio_path, "rb")
        transcription = self.client.audio.transcriptions.create(model=self.model_name, file=audio_file)
        return transcription.text.strip(), num_tokens_from_string(transcription.text.strip())

    def audio2base64(self, audio):
        if isinstance(audio, bytes):
            return base64.b64encode(audio).decode("utf-8")
        if isinstance(audio, io.BytesIO):
            return base64.b64encode(audio.getvalue()).decode("utf-8")
        raise TypeError("The input audio file should be in binary format.")


class GPTSeq2txt(Base):
    _FACTORY_NAME = "OpenAI"

    def __init__(self, key, model_name="whisper-1", base_url="https://api.openai.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name


class QWenSeq2txt(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"

    def __init__(self, key, model_name="qwen-audio-asr", lang="Chinese", **kwargs):
        import dashscope

        dashscope.api_key = key
        self.model_name = model_name
        self.lang = lang

    def transcription(self, audio_path):
        if "paraformer" in self.model_name or "sensevoice" in self.model_name:
            return f"**ERROR**: model {self.model_name} is not suppported yet.", 0

        from dashscope import MultiModalConversation

        audio_path = f"file://{audio_path}"
        prompt_ch = """
        你是一名专业的音频转录助手，能够将MP3音频文件的内容转写为文本，并**精确标记每句话或每个段落对应的时间戳**（开始时间-结束时间）。\n
        **任务要求**：
        1.输入是MP3,解析带时间戳的文本。
        2.时间戳格式为 `[HH:MM:SS.mmm]`（毫秒可选），例如 `[00:01:23.456]`。
        3.时间戳需尽可能贴近实际语音的起止时间，误差不超过1秒。
        4.如果无法确定具体时间，请根据上下文合理估算。
        5.最后总结:这段音频在说什么?
        
        **示例输出**：
        [00:00:00.000] 今天天气真好，
        [00:00:02.500] 我们一起去公园散步吧。
        [00:00:05.800] 公园里的花开得非常漂亮。
        这段音频讲述的是一个关于**“吃水不忘挖井人”**的感人故事，主 ..."""
        prompt_en = """
        You are a professional audio transcription assistant, capable of transcribing the content of MP3 audio files into text and **precisely marking the timestamps (start time - end time) corresponding to each sentence or paragraph**. 
        **Task requirements**: 
        1. Input is MP3, parse text with timestamps.
        2. The timestamp format is `[HH:MM:SS.mmm]` (milliseconds are optional), for example, `[00:01:23.456]`.
        3. The timestamp should be as close as possible to the actual start and end time of the voice, with an error not exceeding 1 second.
        4. If a specific time cannot be determined, please make a reasonable estimation based on the context.
        5. Final summary: What is this audio talking about?
        
        **Example Output**: 
        [00:00:00.000] The weather is really nice today, 
        [00:00:02.500] let's go for a walk in the park together.
        [00:00:05.800] The flowers in the park are blooming beautifully.
        This audio tells a touching story about **"Remembering the one who dug the well when drinking water"** .."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "audio": audio_path
                    },
                    {
                        "text": prompt_ch if self.lang.lower() == "chinese" else prompt_en,
                    },
                ],
            }
        ]

        response = None
        full_content = ""
        try:
            response = MultiModalConversation.call(model=self.model_name, messages=messages, result_format="message", stream=True)
            for response in response:
                try:
                    full_content += response["output"]["choices"][0]["message"].content[0]["text"]
                except Exception:
                    pass
            return full_content, num_tokens_from_string(full_content)
        except Exception as e:
            return "**ERROR**: " + str(e), 0


class AzureSeq2txt(Base):
    _FACTORY_NAME = "Azure-OpenAI"

    def __init__(self, key, model_name, lang="Chinese", **kwargs):
        self.client = AzureOpenAI(api_key=key, azure_endpoint=kwargs["base_url"], api_version="2024-02-01")
        self.model_name = model_name
        self.lang = lang


class XinferenceSeq2txt(Base):
    _FACTORY_NAME = "Xinference"

    def __init__(self, key, model_name="whisper-small", **kwargs):
        self.base_url = kwargs.get("base_url", None)
        self.model_name = model_name
        self.key = key

    def transcription(self, audio, language="zh", prompt=None, response_format="json", temperature=0.7):
        if isinstance(audio, str):
            audio_file = open(audio, "rb")
            audio_data = audio_file.read()
            audio_file_name = audio.split("/")[-1]
        else:
            audio_data = audio
            audio_file_name = "audio.wav"

        payload = {"model": self.model_name, "language": language, "prompt": prompt, "response_format": response_format, "temperature": temperature}

        files = {"file": (audio_file_name, audio_data, "audio/wav")}

        try:
            response = requests.post(f"{self.base_url}/v1/audio/transcriptions", files=files, data=payload)
            response.raise_for_status()
            result = response.json()

            if "text" in result:
                transcription_text = result["text"].strip()
                return transcription_text, num_tokens_from_string(transcription_text)
            else:
                return "**ERROR**: Failed to retrieve transcription.", 0

        except requests.exceptions.RequestException as e:
            return f"**ERROR**: {str(e)}", 0


class GPUStackSeq2txt(Base):
    _FACTORY_NAME = "GPUStack"

    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("url cannot be None")
        if base_url.split("/")[-1] != "v1":
            base_url = os.path.join(base_url, "v1")
        self.base_url = base_url
        self.model_name = model_name
        self.key = key


class ZhipuSeq2txt(Base):
    _FACTORY_NAME = "ZHIPU-AI"

    def __init__(self, key, model_name="glm-asr", base_url="https://open.bigmodel.cn/api/paas/v4", **kwargs):
        if not base_url:
            base_url = "https://open.bigmodel.cn/api/paas/v4"
        self.base_url = base_url
        self.api_key = key
        self.model_name = model_name
        self.gen_conf = kwargs.get("gen_conf", {})
        self.stream = kwargs.get("stream", False)

    def transcription(self, audio_path):
        payload = {
            "model": self.model_name,
            "temperature": str(self.gen_conf.get("temperature", 0.75)) or "0.75",
            "stream": self.stream,
        }

        headers = {"Authorization": f"Bearer {self.api_key}"}
        with open(audio_path, "rb") as audio_file:
            files = {"file": audio_file}

            try:
                response = requests.post(
                    url=f"{self.base_url}/audio/transcriptions",
                    data=payload,
                    files=files,
                    headers=headers,
                )
                body = response.json()
                if response.status_code == 200:
                    full_content = body["text"]
                    return full_content, num_tokens_from_string(full_content)
                else:
                    error = body["error"]
                    return f"**ERROR**: code: {error['code']}, message: {error['message']}", 0
            except Exception as e:
                return "**ERROR**: " + str(e), 0
