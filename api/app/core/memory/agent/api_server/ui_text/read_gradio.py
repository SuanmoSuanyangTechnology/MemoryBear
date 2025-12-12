# import gradio as gr
# import requests
# from app.core.config import settings

# api_url = f"http://{settings.SERVER_IP}:9200/read_service"
# # 定义聊天函数
# def send_message(user_id, message, history_str, search_switch):
#     # history 传入的是字符串，需要转换为列表
#     try:
#         history = eval(history_str) if history_str else []
#     except:
#         history = []

#     input_message = {
#         "user_id": user_id,
#         "message": message,
#         "history": history,
#         "search_switch": search_switch
#     }
#     try:
#         response = requests.post(api_url, json=input_message, timeout=290)
#         result = response.json()  # 假设 API 返回的是 JSON
#     except Exception as e:
#         result = {"error": str(e)}

#     return str(result)


# # Gradio 界面
# with gr.Blocks() as demo:
#     gr.Markdown("## 与 API 对话 Demo")

#     with gr.Row():
#         user_id = gr.Textbox(label="User ID", value="user1")
#         message = gr.Textbox(label="Message", value="请输入消息")

#     history = gr.Textbox(label="History (Python 列表格式)", value="[]")
#     search_switch = gr.Dropdown(label="Search Switch", choices=["0", "1","2"], value="True")

#     submit_btn = gr.Button("发送")
#     output = gr.Textbox(label="API 返回结果")

#     submit_btn.click(
#         send_message,
#         inputs=[user_id, message, history, search_switch],
#         outputs=[output]
#     )

# # 启动界面
# demo.launch()
