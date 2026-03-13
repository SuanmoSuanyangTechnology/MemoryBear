"""
音频转文本服务

支持的服务商:
- DashScope (阿里云通义千问)
- OpenAI Whisper
"""
import httpx

from app.core.logging_config import get_business_logger

logger = get_business_logger()


class AudioTranscriptionService:
    """音频转文本服务"""

    @staticmethod
    async def transcribe_dashscope(audio_url: str, api_key: str) -> str:
        """
        使用阿里云通义千问语音识别服务转换音频为文本
        
        Args:
            audio_url: 音频文件 URL
            api_key: DashScope API Key
            
        Returns:
            str: 转录的文本
        """
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "X-DashScope-Async": "enable",
                    },
                    json={
                        "model": "paraformer-v2",
                        "input": {
                            "file_urls": [audio_url]
                        },
                        "parameters": {
                            "language_hints": ["zh", "en", "ja", "yue", "ko", "de", "fr", "ru"]
                        }
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                if result.get("output", {}).get("results"):
                    text = result["output"]["results"][0].get("transcription_text", "")
                    logger.info(f"音频转文本成功: {len(text)} 字符")
                    return text
                
                return "[音频转文本失败]"
                
        except Exception as e:
            logger.error(f"DashScope 音频转文本失败: {e}")
            return f"[音频转文本失败: {str(e)}]"

    @staticmethod
    async def transcribe_openai(audio_url: str, api_key: str) -> str:
        """
        使用 OpenAI Whisper 转换音频为文本
        
        Args:
            audio_url: 音频文件 URL
            api_key: OpenAI API Key
            
        Returns:
            str: 转录的文本
        """
        try:
            # 下载音频文件
            async with httpx.AsyncClient(timeout=60.0) as client:
                audio_response = await client.get(audio_url, follow_redirects=True)
                audio_response.raise_for_status()
                audio_data = audio_response.content
                
                # 调用 Whisper API
                files = {"file": ("audio.mp3", audio_data, "audio/mpeg")}
                data = {"model": "whisper-1"}
                
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files=files,
                    data=data
                )
                response.raise_for_status()
                result = response.json()
                
                text = result.get("text", "")
                logger.info(f"音频转文本成功: {len(text)} 字符")
                return text
                
        except Exception as e:
            logger.error(f"OpenAI Whisper 音频转文本失败: {e}")
            return f"[音频转文本失败: {str(e)}]"
