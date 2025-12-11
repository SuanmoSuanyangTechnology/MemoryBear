import asyncio
import re

from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_, picture_model_requests,Picture_recognize, Voice_recognize
from app.core.memory.agent.utils.messages_tool import read_template_file

import requests
import json
import os
import time
# file_urls = [
#     "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav",
#     "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_male2.wav",
# ]
class Vico_recognition:
    def __init__(self,file_urls):
        self.api_key=''
        self.backend_model_name=''
        self.api_base=''
        self.file_urls=file_urls

    # 提交文件转写任务，包含待转写文件url列表
    async  def submit_task(self) -> str:
        self.api_key, self.backend_model_name, self.api_base =await Voice_recognize()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        data = {
            "model": self.backend_model_name,
            "input": {"file_urls": self.file_urls},
            "parameters": {
                "channel_id": [0],
                "vocabulary_id": "vocab-Xxxx",
            },
        }
        # 录音文件转写服务url
        service_url = (
            "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
        )
        response = requests.post(
            service_url, headers=headers, data=json.dumps(data)
        )

        # 打印响应内容
        if response.status_code == 200:
            return response.json()["output"]["task_id"]
        else:
            print("task failed!")
            print(response.json())
            return None

    async def download_transcription_result(self, transcription_url):
        """
        Args:
            transcription_url (str): 转写结果文件URL
        Returns:
            dict: 转写结果内容
        """
        try:
            response = requests.get(transcription_url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"下载转写结果失败: {e}")
            return None

    # 循环查询任务状态直到成功
    async def wait_for_complete(self,task_id):
        self.api_key, self.backend_model_name, self.api_base = await Voice_recognize()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        pending = True
        while pending:
            # 查询任务状态服务url
            service_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
            response = requests.post(
                service_url, headers=headers
            )
            if response.status_code == 200:
                status = response.json()['output']['task_status']
                if status == 'SUCCEEDED':
                    print("task succeeded!")
                    pending = False
                    return response.json()['output']['results']
                elif status == 'RUNNING' or status == 'PENDING':
                    pass
                else:
                    print("task failed!")
                    pending = False
            else:
                print("query failed!")
                pending = False
            time.sleep(0.1)
    async def run(self):
        self.api_key, self.backend_model_name, self.api_base = await Voice_recognize()
        task_id=await self.submit_task()
        result=await self.wait_for_complete(task_id)
        result_context=[]
        for  i in result:
            transcription_url=i['transcription_url']
            print(f"转写URL: {transcription_url}")

            # 下载并打印转写内容
            content = await self.download_transcription_result(transcription_url)
            if content:
                content=json.dumps(content, indent=2, ensure_ascii=False)
                context=re.findall(r'"text": "(.*?)"', content)
                result_context.append(context[0])
        result=''.join(result_context)
        return (result)




