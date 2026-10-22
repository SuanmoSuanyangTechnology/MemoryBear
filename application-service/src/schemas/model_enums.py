"""Model vocabulary shared by DSL import/export and node configs (subset of
the host ``app/models/models_model.py`` enums).

The host defines these next to its ORM models; this service only needs the
literal value domains for validation and SQL filters against the host-owned
``model_configs`` mirror, so the ORM coupling is dropped and the values stay
byte-compatible with the host.
"""

from enum import StrEnum


class ModelType(StrEnum):
    """Model type enum"""
    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    ASR = "asr"
    IMAGE = "image"
    VIDEO = "video"

    @classmethod
    def _missing_(cls, value):
        """Legacy-string read-side normalization: `"chat"` → LLM (compat for old DB/YAML rows); `"asr"` → ASR (case-tolerant)."""
        if isinstance(value, str):
            if value.lower() == "chat":
                return cls.LLM
            if value.lower() == "asr":
                return cls.ASR
        return None


# Legacy-type read-side compatibility (CHAT member removed; old YAML / old images may still
# send or store "chat"): always use this constant for set / SQL comparisons, never ModelType.CHAT
LEGACY_CHAT_TYPE = "chat"
# LLM family (including legacy chat): shared by set checks / SQL IN; tuple order is the SQL literal order
LLM_FAMILY_TYPES = (ModelType.LLM.value, LEGACY_CHAT_TYPE)


class ModelFeature(StrEnum):
    """Capability flags (the three-column features value domain of contract v2,
    same vocabulary as the redbear-model package)"""
    THINKING = "thinking"
    THINKING_ONLY = "thinking_only"
    JSON_OUTPUT = "json_output"
    FUNCTION_CALL = "function_call"


class ModelProvider(StrEnum):
    """Model provider enum"""
    OPENAI = "openai"
    SPEEDBEAR = "speedbear"
    MINIMAX = "minimax"
    # ANTHROPIC = "anthropic"
    # GOOGLE = "google"
    # BAIDU = "baidu"
    DASHSCOPE = "dashscope"
    # ZHIPU = "zhipu"
    # MOONSHOT = "moonshot"
    # DEEPSEEK = "deepseek"
    OLLAMA = "ollama"
    XINFERENCE = "xinference"
    GPUSTACK = "gpustack"
    BEDROCK = "bedrock"
    VOLCANO = "volcano"
    COMPOSITE = "composite"


__all__ = [
    "LEGACY_CHAT_TYPE",
    "LLM_FAMILY_TYPES",
    "ModelFeature",
    "ModelProvider",
    "ModelType",
]
