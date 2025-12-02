import requests
import json
import uuid
import os
import time
from datetime import datetime
from app.core.config import settings


# 火山的ASR
class VolcASR:
    def __init__(self):
        self.app_key = settings.VOLC_APP_KEY  # 需要替换为实际的APP KEY
        self.access_key = settings.VOLC_ACCESS_KEY  # 需要替换为实际的Access Key
        self.submit_url = settings.VOLC_SUBMIT_URL
        self.query_url = settings.VOLC_QUERY_URL

    def get_headers(self):
        request_id = str(uuid.uuid4())
        return {
            "X-Api-App-Key": self.app_key,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": "volc.bigasr.auc",
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
            "Content-Type": "application/json"
        }

    def submit_task(self, audio_url):
        headers = self.get_headers()
        data = {
            "audio": {
                "url": audio_url
            }
        }

        response = requests.post(self.submit_url, headers=headers, json=data)
        return response.headers.get("X-Api-Request-Id"), response.headers.get("X-Api-Status-Code")

    def query_result(self, task_id):
        headers = self.get_headers()
        headers["X-Api-Request-Id"] = task_id

        while True:
            response = requests.post(self.query_url, headers=headers, json={})
            status_code = response.headers.get("X-Api-Status-Code")

            if status_code == "20000000":  # 成功
                return response.json()
            elif status_code in ["20000001", "20000002"]:  # 处理中或在队列中
                time.sleep(1)
                continue
            elif status_code in ["20000003"]:  # 静音音频
                raise Exception(f"静音音频: {status_code}")
            elif status_code in ["45000001"]:  # 请求参数无效
                raise Exception(f"请求参数无效: {status_code}")
            elif status_code in ["45000002"]:  # 空音频
                raise Exception(f"空音频: {status_code}")
            elif status_code in ["45000151"]:  # 音频格式不正确
                raise Exception(f"音频格式不正确: {status_code}")
            elif status_code in ["55000031"]:  # 服务器繁忙
                raise Exception(f"服务器繁忙: {status_code}")
            else:
                raise Exception(f"服务内部处理错误: {status_code}")


def main():
    # 音频URL
    audio_url = "https://fosun-lcp-clickpaas.oss-cn-shanghai.aliyuncs.com/fosun-dify-files-images/test.mp3"

    # 输出目录
    output_dir = "/Users/sbtjfdn/Downloads"
    os.makedirs(output_dir, exist_ok=True)

    # 初始化ASR客户端
    asr_client = VolcASR()

    try:
        # 提交任务
        print("提交语音识别任务...")
        task_id, status_code = asr_client.submit_task(audio_url)
        if not task_id:
            raise Exception("提交任务失败，未获取到任务ID")

        print(f"任务ID: {task_id}")

        # 查询结果
        print("等待识别结果...")
        result = asr_client.query_result(task_id)

        # 保存结果
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(output_dir, f"result_{timestamp}.json")

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"识别结果已保存到: {output_file}")

        # 如果有识别文本，单独保存文本文件
        if "result" in result and "text" in result["result"]:
            text_file = os.path.join(output_dir, f"text_{timestamp}.txt")
            with open(text_file, "w", encoding="utf-8") as f:
                f.write(result["result"]["text"])
            print(f"识别文本已保存到: {text_file}")

    except Exception as e:
        print(f"发生错误: {str(e)}")


if __name__ == "__main__":
    main()