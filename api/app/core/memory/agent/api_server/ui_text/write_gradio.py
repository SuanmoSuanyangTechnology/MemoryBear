import os
import gradio as gr
import requests
from dotenv import load_dotenv

# 读取环境变量
load_dotenv()
server_api_url = os.getenv("SERVER_IP")
api_url = f"http://{server_api_url}:9200/writer_service"


# 定义发送函数
def send_message(user_id, message):
    input_message = {
        "user_id": user_id,
        "message": message,
        "history": []
    }
    try:
        response = requests.post(api_url, json=input_message, timeout=90)
        # 假设服务返回 JSON
        result = response.json()
    except Exception as e:
        result = {"error": str(e)}
    return str(result)


# Gradio UI 构建
with gr.Blocks() as demo:
    gr.Markdown("Writer Service 对话界面")

    user_id = gr.Textbox(label="User ID", value="user1", placeholder="请输入用户ID")
    message = gr.Textbox(label="Message", placeholder="请输入要发送的信息")

    send_btn = gr.Button("发送")
    result_box = gr.Textbox(label="返回结果")

    send_btn.click(
        fn=send_message,
        inputs=[user_id, message],
        outputs=[result_box]
    )

# 启动界面
demo.launch()
