

# project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# sys.path.insert(0, project_root)

# load_dotenv()

# async def llm_client_chat(messages: List[dict]) -> str:
#     """使用 OpenAI 兼容接口进行对话，返回内容字符串。"""
#     try:
#         cfg = get_model_config(SELECTED_LLM_ID)
#         rb_config = RedBearModelConfig(
#         model_name=cfg["model_name"],
#         provider=cfg["provider"],
#         api_key=cfg["api_key"],
#         base_url=cfg["base_url"],
#     )
#         client = OpenAIClient(model_config=rb_config, type_="chat")

#     except Exception as e:
#         logger.error(f"获取模型配置失败：{e}")
#         err = f"获取模型配置失败：{str(e)}。请检查!!!"
#         return err
#     try:
#         response = await client.chat(messages)
#         print(f"model_tool's llm_client_chat response ======>:\n {response}")
#         return _extract_content(response)
#         # return _extract_content(result)
#     except Exception as e:
#         logger.error(f"LLM调用失败：{str(e)}。请检查 model_name、api_key、api_base 是否正确。")
#     return f"LLM调用失败：{str(e)}。请检查 model_name、api_key、api_base 是否正确。"

# async def main(image_url):
#     await llm_client_chat(image_url)
#
# # 运行主函数
# asyncio.run(main(['https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_male2.wav']))
#
