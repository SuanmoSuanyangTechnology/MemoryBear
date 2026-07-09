"""
Sandbox Runtime Configuration

所有配置通过环境变量注入（由 orchestrator 在创建 sandbox 时设置）
"""
import os
from dataclasses import dataclass, field


@dataclass
class SandboxRuntimeConfig:
    """Sandbox 运行时配置"""

    # ─── LLM 配置 ───
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_model_name: str = ""
    llm_provider: str = "openai"

    # ─── 回调 API（sandbox → 主 API）───
    callback_url: str = ""  # 主 API 的内部回调地址
    callback_secret: str = ""  # 认证 secret

    # ─── 执行上下文 ───
    workspace_id: str = ""
    user_id: str = ""
    execution_id: str = ""
    conversation_id: str = ""

    # ─── 资源限制 ───
    max_execution_time: int = 300  # 最大执行时间（秒）
    max_tool_calls: int = 20  # 单次运行最大工具调用次数
    enable_network: bool = True  # 是否允许网络访问

    # ─── 存储 ───
    output_dir: str = "/app/output"
    workspace_dir: str = "/app/workspace"

    @classmethod
    def from_env(cls) -> "SandboxRuntimeConfig":
        """从环境变量加载配置"""
        return cls(
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_api_base=os.getenv("LLM_API_BASE", ""),
            llm_model_name=os.getenv("LLM_MODEL_NAME", ""),
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            callback_url=os.getenv("CALLBACK_URL", ""),
            callback_secret=os.getenv("CALLBACK_SECRET", ""),
            workspace_id=os.getenv("WORKSPACE_ID", ""),
            user_id=os.getenv("USER_ID", ""),
            execution_id=os.getenv("EXECUTION_ID", ""),
            conversation_id=os.getenv("CONVERSATION_ID", ""),
            max_execution_time=int(os.getenv("MAX_EXECUTION_TIME", "300")),
            max_tool_calls=int(os.getenv("MAX_TOOL_CALLS", "20")),
            enable_network=os.getenv("ENABLE_NETWORK", "true").lower() == "true",
            output_dir=os.getenv("OUTPUT_DIR", "/app/output"),
            workspace_dir=os.getenv("WORKSPACE_DIR", "/app/workspace"),
        )
