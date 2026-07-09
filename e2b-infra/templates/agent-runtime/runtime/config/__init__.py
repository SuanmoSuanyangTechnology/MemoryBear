"""Sandbox Runtime Configuration - loaded from environment variables"""
import os
from dataclasses import dataclass


@dataclass
class SandboxRuntimeConfig:
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_model_name: str = ""
    llm_provider: str = "openai"
    callback_url: str = ""
    callback_secret: str = ""
    workspace_id: str = ""
    user_id: str = ""
    execution_id: str = ""
    conversation_id: str = ""
    max_execution_time: int = 300
    enable_network: bool = True

    @classmethod
    def from_env(cls) -> "SandboxRuntimeConfig":
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
            enable_network=os.getenv("ENABLE_NETWORK", "true").lower() == "true",
        )
