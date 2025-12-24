import base64
import json
import os
import tempfile
import logging
from abc import ABC
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin
import requests
from openai import OpenAI
from openai.lib.azure import AzureOpenAI
from app.core.rag.nlp import is_english
from app.core.rag.prompts.generator import vision_llm_describe_prompt
from app.core.rag.common.token_utils import num_tokens_from_string, total_token_count_from_response


class Base(ABC):
    def __init__(self, **kwargs):
        # Configure retry parameters
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        self.max_rounds = kwargs.get("max_rounds", 5)
        self.is_tools = False
        self.tools = []
        self.toolcall_sessions = {}
        self.extra_body = None

    def describe(self, image):
        raise NotImplementedError("Please implement encode method!")

    def describe_with_prompt(self, image, prompt=None):
        raise NotImplementedError("Please implement encode method!")

    def _form_history(self, system, history, images=None):
        hist = []
        if system:
            hist.append({"role": "system", "content": system})
        for h in history:
            if images and h["role"] == "user":
                h["content"] = self._image_prompt(h["content"], images)
                images = []
            hist.append(h)
        return hist

    def _image_prompt(self, text, images):
        if not images:
            return text

        if isinstance(images, str) or "bytes" in type(images).__name__:
            images = [images]

        pmpt = [{"type": "text", "text": text}]
        for img in images:
            pmpt.append({
                "type": "image_url",
                "image_url": {
                    "url": img if isinstance(img, str) and img.startswith("data:") else f"data:image/png;base64,{img}"
                }
            })
        return pmpt

    def chat(self, system, history, gen_conf, images=None, **kwargs):
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=self._form_history(system, history, images),
                extra_body=self.extra_body,
            )
            return response.choices[0].message.content.strip(), response.usage.total_tokens
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system, history, gen_conf, images=None, **kwargs):
        ans = ""
        tk_count = 0
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=self._form_history(system, history, images),
                stream=True,
                extra_body=self.extra_body,
            )
            for resp in response:
                if not resp.choices[0].delta.content:
                    continue
                delta = resp.choices[0].delta.content
                ans = delta
                if resp.choices[0].finish_reason == "length":
                    ans += "...\nFor the content length reason, it stopped, continue?" if is_english([ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
                if resp.choices[0].finish_reason == "stop":
                    tk_count += resp.usage.total_tokens
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield tk_count

    @staticmethod
    def image2base64_rawvalue(self, image):
        # Return a base64 string without data URL header
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            return b64
        if isinstance(image, BytesIO):
            data = image.getvalue()
            b64 = base64.b64encode(data).decode("utf-8")
            return b64
        with BytesIO() as buffered:
            try:
                image.save(buffered, format="JPEG")
            except Exception:
                 # reset buffer before saving PNG
                buffered.seek(0)
                buffered.truncate()
                image.save(buffered, format="PNG")
            data = buffered.getvalue()
            b64 = base64.b64encode(data).decode("utf-8")
        return b64

    @staticmethod
    def image2base64(image):
        # Return a data URL with the correct MIME to avoid provider mismatches
        if isinstance(image, bytes):
            # Best-effort magic number sniffing
            mime = "image/png"
            if len(image) >= 2 and image[0] == 0xFF and image[1] == 0xD8:
                mime = "image/jpeg"
            b64 = base64.b64encode(image).decode("utf-8")
            return f"data:{mime};base64,{b64}"
        if isinstance(image, BytesIO):
            data = image.getvalue()
            mime = "image/png"
            if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
                mime = "image/jpeg"
            b64 = base64.b64encode(data).decode("utf-8")
            return f"data:{mime};base64,{b64}"
        with BytesIO() as buffered:
            fmt = "jpeg"
            try:
                image.save(buffered, format="JPEG")
            except Exception:
                 # reset buffer before saving PNG
                buffered.seek(0)
                buffered.truncate()
                image.save(buffered, format="PNG")
                fmt = "png"
            data = buffered.getvalue()
            b64 = base64.b64encode(data).decode("utf-8")
            mime = f"image/{fmt}"
        return f"data:{mime};base64,{b64}"

    def prompt(self, b64):
        return [
            {
                "role": "user",
                "content": self._image_prompt(
                    "请用中文详细描述一下图中的内容，比如时间，地点，人物，事情，人物心情等，如果有数据请提取出数据。"
                    if self.lang.lower() == "chinese"
                    else "Please describe the content of this picture, like where, when, who, what happen. If it has number data, please extract them out.",
                    b64
                )
            }
        ]

    def vision_llm_prompt(self, b64, prompt=None):
        return [
            {
                "role": "user",
                "content": self._image_prompt(prompt if prompt else vision_llm_describe_prompt(), b64)
            }
        ]


class GptV4(Base):
    _FACTORY_NAME = "OpenAI"

    def __init__(self, key, model_name="gpt-4-vision-preview", lang="Chinese", base_url="https://api.openai.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.api_key = key
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name
        self.lang = lang
        super().__init__(**kwargs)

    def describe(self, image):
        b64 = self.image2base64(image)
        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=self.prompt(b64),
            extra_body=self.extra_body,
        )
        return res.choices[0].message.content.strip(), total_token_count_from_response(res)

    def describe_with_prompt(self, image, prompt=None):
        b64 = self.image2base64(image)
        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=self.vision_llm_prompt(b64, prompt),
            extra_body=self.extra_body,
        )
        return res.choices[0].message.content.strip(),total_token_count_from_response(res)


class AzureGptV4(GptV4):
    _FACTORY_NAME = "Azure-OpenAI"

    def __init__(self, key, model_name, lang="Chinese", **kwargs):
        api_key = json.loads(key).get("api_key", "")
        api_version = json.loads(key).get("api_version", "2024-02-01")
        self.client = AzureOpenAI(api_key=api_key, azure_endpoint=kwargs["base_url"], api_version=api_version)
        self.model_name = model_name
        self.lang = lang
        Base.__init__(self, **kwargs)


class QWenCV(GptV4):
    _FACTORY_NAME = "Tongyi-Qianwen"

    def __init__(self, key, model_name="qwen-vl-chat-v1", lang="Chinese", base_url=None, **kwargs):
        if not base_url:
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        super().__init__(key, model_name, lang=lang, base_url=base_url, **kwargs)

    def chat(self, system, history, gen_conf, images=None, video_bytes=None, filename="", **kwargs):
        if video_bytes:
            try:
                summary, summary_num_tokens = self._process_video(video_bytes, filename)
                return summary, summary_num_tokens
            except Exception as e:
                return "**ERROR**: " + str(e), 0

        return "**ERROR**: Method chat not supported yet.", 0

    def _process_video(self, video_bytes, filename):
        from dashscope import MultiModalConversation

        video_suffix = Path(filename).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=video_suffix) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

            video_path = f"file://{tmp_path}"
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "video": video_path,
                            "fps": 2,
                        },
                        {
                            "text": "视频的内容是什么?,并且，请用恰当的句子总结这个视频。" if self.lang.lower() == "chinese" else "What is the content of the video? And please summarize this video in proper sentences.",
                        },
                    ],
                }
            ]

            def call_api():
                response = MultiModalConversation.call(
                    api_key=self.api_key,
                    model=self.model_name,
                    messages=messages,
                )
                summary = response["output"]["choices"][0]["message"].content[0]["text"]
                return summary, num_tokens_from_string(summary)

            try:
                return call_api()
            except Exception as e1:
                import dashscope

                dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"
                try:
                    return call_api()
                except Exception as e2:
                    raise RuntimeError(f"Both default and intl endpoint failed.\nFirst error: {e1}\nSecond error: {e2}")


class XinferenceCV(GptV4):
    _FACTORY_NAME = "Xinference"

    def __init__(self, key, model_name="", lang="Chinese", base_url="", **kwargs):
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name
        self.lang = lang
        Base.__init__(self, **kwargs)


class GPUStackCV(GptV4):
    _FACTORY_NAME = "GPUStack"

    def __init__(self, key, model_name, lang="Chinese", base_url="", **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name
        self.lang = lang
        Base.__init__(self, **kwargs)


class OllamaCV(Base):
    _FACTORY_NAME = "Ollama"

    def __init__(self, key, model_name, lang="Chinese", **kwargs):
        from ollama import Client
        self.client = Client(host=kwargs["base_url"])
        self.model_name = model_name
        self.lang = lang
        self.keep_alive = kwargs.get("ollama_keep_alive", int(os.environ.get("OLLAMA_KEEP_ALIVE", -1)))
        Base.__init__(self, **kwargs)


    def _clean_img(self, img):
        if not isinstance(img, str):
            return img

        #remove the header like "data/*;base64,"
        if img.startswith("data:") and ";base64," in img:
            img = img.split(";base64,")[1]
        return img

    def _clean_conf(self, gen_conf):
        options = {}
        if "temperature" in gen_conf:
            options["temperature"] = gen_conf["temperature"]
        if "top_p" in gen_conf:
            options["top_k"] = gen_conf["top_p"]
        if "presence_penalty" in gen_conf:
            options["presence_penalty"] = gen_conf["presence_penalty"]
        if "frequency_penalty" in gen_conf:
            options["frequency_penalty"] = gen_conf["frequency_penalty"]
        return options

    def _form_history(self, system, history, images=None):
        hist = deepcopy(history)
        if system and hist[0]["role"] == "user":
            hist.insert(0, {"role": "system", "content": system})
        if not images:
            return hist
        temp_images = []
        for img in images:
            temp_images.append(self._clean_img(img))
        for his in hist:
            if his["role"] == "user":
                his["images"] = temp_images
                break
        return hist

    def describe(self, image):
        prompt = self.prompt("")
        try:
            response = self.client.generate(
                model=self.model_name,
                prompt=prompt[0]["content"],
                images=[image],
            )
            ans = response["response"].strip()
            return ans, 128
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def describe_with_prompt(self, image, prompt=None):
        vision_prompt = self.vision_llm_prompt("", prompt) if prompt else self.vision_llm_prompt("")
        try:
            response = self.client.generate(
                model=self.model_name,
                prompt=vision_prompt[0]["content"],
                images=[image],
            )
            ans = response["response"].strip()
            return ans, 128
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat(self, system, history, gen_conf, images=None, **kwargs):
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=self._form_history(system, history, images),
                options=self._clean_conf(gen_conf),
                keep_alive=self.keep_alive
            )

            ans = response["message"]["content"].strip()
            return ans, response["eval_count"] + response.get("prompt_eval_count", 0)
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system, history, gen_conf, images=None, **kwargs):
        ans = ""
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=self._form_history(system, history, images),
                stream=True,
                options=self._clean_conf(gen_conf),
                keep_alive=self.keep_alive
            )
            for resp in response:
                if resp["done"]:
                    yield resp.get("prompt_eval_count", 0) + resp.get("eval_count", 0)
                ans = resp["message"]["content"]
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)
        yield 0


if __name__ == "__main__":
    # import sys
    # chunk(sys.argv[1], from_page=0, to_page=10, callback=dummy)

    # # 准备配置vision_model信息
    # azure_config = {
    #     "api_key": "xxxxx",
    #     "api_version": "2024-02-01"
    # }
    # # 转换为 JSON 字符串，因为类中使用 json.loads(key) 解析
    # key = json.dumps(azure_config)
    # # 初始化 AzureGptV4
    # vision_model = AzureGptV4(
    #     key=key,  # JSON 字符串形式的配置
    #     model_name="gpt-4o",
    #     lang="Chinese",  # 默认使用中文
    #     base_url="https://fosun-openai-east-us-001.openai.azure.com/"  # Azure OpenAI 端点
    # )
    # try:
    #     # 测试图像描述功能
    #     image_path = "/Users/sbtjfdn/Downloads/记忆科学/files/aippt.cn.png"
    #     with open(image_path, "rb") as image_file:
    #         image_data = image_file.read()
    #
    #     # 使用 describe 方法
    #     description, token_count = vision_model.describe(image_data)
    #     # from app.core.rag.prompts.generator import vision_llm_figure_describe_prompt
    #     # description, token_count = vision_model.describe_with_prompt(image_data, prompt=vision_llm_figure_describe_prompt())
    #     print(f"描述: {description}")
    #     print(f"使用的令牌数: {token_count}")
    #
    # except Exception as e:
    #     print(f"初始化或处理过程中出错: {str(e)}")

    # 准备配置vision_model信息
    # 初始化 QWenCV
    vision_model = QWenCV(
        key="sk-8e9e40cd171749858ce2d3722ea75669",
        model_name="qwen-vl-max",
        lang="Chinese",  # 默认使用中文
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    try:
        # 测试图像描述功能
        image_path = "/Users/sbtjfdn/Downloads/记忆科学/files/10.png"
        with open(image_path, "rb") as image_file:
            image_data = image_file.read()

        # 使用 describe 方法
        description, token_count = vision_model.describe(image_data)
        # from app.core.rag.prompts.generator import vision_llm_figure_describe_prompt
        # description, token_count = vision_model.describe_with_prompt(image_data, prompt=vision_llm_figure_describe_prompt())
        print(f"描述: {description}")
        print(f"使用的令牌数: {token_count}")

    except Exception as e:
        print(f"初始化或处理过程中出错: {str(e)}")
