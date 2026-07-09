"""
MemoryBear Sandbox Runtime

独立的 Agent/Workflow 执行引擎，设计为在 E2B sandbox（容器/microVM）内运行。
不依赖数据库、Celery 或其他基础设施组件。

与主 API 通过 HTTP 回调协议通信，获取工具执行结果、知识库检索等。
"""
