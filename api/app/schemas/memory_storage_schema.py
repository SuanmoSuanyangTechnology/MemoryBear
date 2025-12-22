"""
所有的内容是放错误地方了，应该放在models
"""

from typing import Any, Optional, List, Dict, Literal, Union
import time
import uuid
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


# ============================================================================
# 原 UserInput 相关 Schema (保留原有功能)
# ============================================================================
class UserInput(BaseModel):
    message: str
    history: list[dict]
    search_switch: str
    group_id: str


class Write_UserInput(BaseModel):
    message: str
    group_id: str


# ============================================================================
# 从 json_schema.py 迁移的 Schema
# ============================================================================
class BaseDataSchema(BaseModel):
    """Base schema for the data"""
    # 保持原有必需字段为可选，以兼容不同数据源
    id: Optional[str] = Field(None, description="The unique identifier for the data entry.")
    statement: Optional[str] = Field(None, description="The statement text.")
    created_at: str = Field(..., description="The creation timestamp in ISO 8601 format.")
    expired_at: Optional[str] = Field(None, description="The expiration timestamp in ISO 8601 format.")
    description: Optional[str] = Field(None, description="The description of the data entry.")

    # 新增字段以匹配实际输入数据
    entity1_name: str = Field(..., description="The first entity name.")
    entity2_name: Optional[str] = Field(None, description="The second entity name.")
    statement_id: str = Field(..., description="The statement identifier.")
    relationship_type: str = Field(..., description="The relationship type.")
    relationship: Optional[Dict[str, Any]] = Field(None, description="The relationship object.")
    entity2: Optional[Dict[str, Any]] = Field(None, description="The second entity object.")


class QualityAssessmentSchema(BaseModel):
    """Schema for memory quality assessment results."""
    score: int = Field(..., ge=0, le=100, description="Quality score percentage (0-100).")
    summary: str = Field(..., description="Brief summary of data quality status, including main issues and strengths.")


class MemoryVerifySchema(BaseModel):
    """Schema for memory privacy verification results."""
    has_privacy: bool = Field(..., description="Whether privacy information was detected.")
    privacy_types: List[str] = Field([], description="List of detected privacy information types.")
    summary: str = Field(..., description="Brief summary of privacy detection results.")


class ConflictResultSchema(BaseModel):
    """Schema for the conflict result data in the reflexion_data.json file."""
    data: List[BaseDataSchema] = Field(..., description="The conflict memory data. Only contains conflicting records when conflict is True.")
    conflict: bool = Field(..., description="Whether the memory is in conflict.")
    quality_assessment: Optional[QualityAssessmentSchema] = Field(None, description="The quality assessment object. Contains score and summary when quality_assessment is enabled, null otherwise.")
    memory_verify: Optional[MemoryVerifySchema] = Field(None, description="The memory privacy verification object. Contains privacy detection results when memory_verify is enabled, null otherwise.")

    @model_validator(mode="before")
    def _normalize_data(cls, v):
        if isinstance(v, dict):
            d = v.get("data")
            if isinstance(d, dict):
                v["data"] = [d]
        return v


class ConflictSchema(BaseModel):
    """Schema for the conflict data in the reflexion_data"""
    data: List[BaseDataSchema] = Field(..., description="The conflict memory data.")
    conflict_memory: Optional[BaseDataSchema] = Field(None, description="The conflict memory data.")

    @model_validator(mode="before")
    def _normalize_data(cls, v):
        if isinstance(v, dict):
            d = v.get("data")
            if isinstance(d, dict):
                v["data"] = [d]
        return v


class ReflexionSchema(BaseModel):
    """Schema for the reflexion data in the reflexion_data"""
    reason: str = Field(..., description="The reason for the reflexion.")
    solution: str = Field(..., description="The solution for the reflexion.")


class ChangeRecordSchema(BaseModel):
    """Schema for individual change records"""
    field: List[Dict[str, str]] = Field(..., description="List of field changes, each containing field name and new value.")

class ResolvedSchema(BaseModel):
    """Schema for the resolved memory data in the reflexion_data"""
    original_memory_id: Optional[str] = Field(None, description="The original memory identifier.")
    # resolved_memory: Optional[BaseDataSchema] = Field(None, description="The resolved memory data (only contains records that need modification).")
    resolved_memory: Optional[Union[BaseDataSchema, List[BaseDataSchema]]] = Field(None, description="The resolved memory data (only contains records that need modification). Can be a single record or list of records.")
    change: Optional[List[ChangeRecordSchema]] = Field(None, description="List of detailed change records with IDs and field information.")


class SingleReflexionResultSchema(BaseModel):
    """Schema for a single reflexion result item."""
    conflict: ConflictResultSchema = Field(..., description="The conflict result data for this specific conflict type.")
    reflexion: ReflexionSchema = Field(..., description="The reflexion data for this conflict.")
    resolved: Optional[ResolvedSchema] = Field(None, description="The resolved memory data for this conflict.")
    type: str = Field("reflexion_result", description="The type identifier.")

class ReflexionResultSchema(BaseModel):
    """Schema for the complete reflexion result data - a list of individual conflict resolutions."""
    results: List[SingleReflexionResultSchema] = Field(..., description="List of individual conflict resolution results, grouped by conflict type.")

    @model_validator(mode="before")
    def _normalize_resolved(cls, v):
        if isinstance(v, dict):
            conflict = v.get("conflict")
            if isinstance(conflict, dict) and conflict.get("conflict") is False:
                v["resolved"] = None
            else:
                resolved = v.get("resolved")
                if isinstance(resolved, dict):
                    orig = resolved.get("original_memory_id")
                    mem = resolved.get("resolved_memory")
                    if orig is None and (mem is None or mem == {}):
                        v["resolved"] = None
        return v


# ============================================================================
# 从 messages.py 迁移的 Schema
# ============================================================================

# Composite key identifying a config row
class ConfigKey(BaseModel):  # 配置参数键模型
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    config_id: int = Field("config_id", description="配置唯一标识（字符串）")
    user_id: str = Field("user_id", description="用户标识（字符串）")
    apply_id: str = Field("apply_id", description="应用或场景标识（字符串）")


# Allowed chunking strategies (extendable later)
ChunkerStrategy = Literal[  # 分块策略枚举
    "RecursiveChunker",
    "TokenChunker",
    "SemanticChunker",
    "NeuralChunker",
    "HybridChunker",
    "LLMChunker",
    "SentenceChunker",
    "LateChunker"
]


# 这是 Request body示例
class ConfigParams(ConfigKey):  # 创建配置参数模型  旧
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Boolean switches
    enable_llm_dedup_blockwise: bool = Field(True, description="启用LLM决策去重")
    enable_llm_disambiguation: bool = Field(True, description="启用LLM决策消歧")
    deep_retrieval: bool = Field(True, description="深度检索开关（保留既有拼写）")

    # Thresholds in [0, 1]
    t_type_strict: float = Field(0.8, ge=0.0, le=1.0, description="类型严格阈值")
    t_name_strict: float = Field(0.8, ge=0.0, le=1.0, description="名称严格阈值")
    t_overall: float = Field(0.8, ge=0.0, le=1.0, description="综合阈值")
    state: bool = Field(False, description="配置使用状态（True/False）")
    # Chunker strategy selection (must be one of the declared literals)
    chunker_strategy: ChunkerStrategy = Field(
        "RecursiveChunker",
        description=(
            "分块策略：RecursiveChunker/TokenChunker/SemanticChunker/NeuralChunker/"
            "HybridChunker/LLMChunker/SentenceChunker/LateChunker"
        ),
    )

    @field_validator("chunker_strategy", mode="before")
    @classmethod
    def map_chunker_aliases(cls, v: str):
        # 允许常见别名并映射到合法枚举
        if isinstance(v, str):
            m = v.strip().lower()
            alias_map = {
                "auto": "RecursiveChunker",
                "by_sentence": "SentenceChunker",
                "by_paragraph": "SemanticChunker",
                "fixed_tokens": "TokenChunker",
                "递归分块": "RecursiveChunker",
                "token 分块": "TokenChunker",
                "token分块": "TokenChunker",
                "语义分块": "SemanticChunker",
                "神经网络分块": "NeuralChunker",
                "混合分块": "HybridChunker",
                "llm 分块": "LLMChunker",
                "llm分块": "LLMChunker",
                "句子分块": "SentenceChunker",
                "延迟分块": "LateChunker",
            }
            if m in alias_map:
                return alias_map[m]
        return v

    @field_validator("config_id", "user_id", "apply_id")
    @classmethod
    def non_empty_str(cls, v: str) -> str:
        s = str(v).strip() if v is not None else ""
        if not s:
            raise ValueError("标识字段必须为非空字符串")
        return s


class ConfigParamsCreate(BaseModel):  # 创建配置参数模型（仅 body，去除主键）
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    config_name: str = Field("配置名称", description="配置名称（字符串）")
    config_desc: str = Field("配置描述", description="配置描述（字符串）")
    workspace_id: Optional[uuid.UUID] = Field(None, description="工作空间ID（UUID）")
    
    # 模型配置字段（可选，用于手动指定或自动填充）
    llm_id: Optional[str] = Field(None, description="LLM模型配置ID")
    embedding_id: Optional[str] = Field(None, description="嵌入模型配置ID")
    rerank_id: Optional[str] = Field(None, description="重排序模型配置ID")


class ConfigParamsDelete(BaseModel):  # 删除配置参数模型（请求体）
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    # config_name: str = Field("配置名称", description="配置名称（字符串）")
    config_id: int = Field("配置ID", description="配置ID（字符串）")


class ConfigUpdate(BaseModel):  # 更新记忆萃取引擎配置参数时使用的模型
    config_id: Optional[int] = None
    config_name: str = Field("配置名称", description="配置名称（字符串）")
    config_desc: str = Field("配置描述", description="配置描述（字符串）")


class ConfigUpdateExtracted(BaseModel):  # 更新记忆萃取引擎配置参数时使用的模型
    config_id: Optional[int] = None
    llm_id: Optional[str] = Field(None, description="LLM模型配置ID")
    embedding_id: Optional[str] = Field(None, description="嵌入模型配置ID")
    rerank_id: Optional[str] = Field(None, description="重排序模型配置ID")
    enable_llm_dedup_blockwise: Optional[bool] = None
    enable_llm_disambiguation: Optional[bool] = None
    deep_retrieval: Optional[bool] = Field(None, validation_alias="deep_retrieval")

    t_type_strict: Optional[float] = Field(None, ge=0.0, le=1.0)
    t_name_strict: Optional[float] = Field(None, ge=0.0, le=1.0)
    t_overall: Optional[float] = Field(None, ge=0.0, le=1.0)
    state: Optional[bool] = None
    chunker_strategy: Optional[ChunkerStrategy] = None
    # 句子提取 
    statement_granularity: Optional[int] = Field(2, ge=1, le=3, description="陈述提取颗粒度，挡位 1/2/3；默认 2")
    include_dialogue_context: Optional[bool] = None
    max_context: Optional[int] = Field(1000, gt=100, description="对话语境中包含字符的最大数量（>100）；默认 1000")

    # 剪枝配置：与 runtime.json 中 pruning 段对应
    pruning_enabled: Optional[bool] = Field(None, description="是否启动智能语义剪枝")
    pruning_scene: Optional[Literal["education", "online_service", "outbound"]] = Field(
        None, description="智能剪枝场景：education/online_service/outbound"
    )
    pruning_threshold: Optional[float] = Field(
        None, ge=0.0, le=0.9, description="智能语义剪枝阈值（0-0.9）"
    )

    # 反思配置
    enable_self_reflexion: Optional[bool] = Field(None, description="是否启用自我反思")
    iteration_period: Optional[Literal["1", "3", "6", "12", "24"]] = Field(
        "3", description="反思迭代周期，单位小时"
    )
    reflexion_range: Optional[Literal["retrieval", "database"]] = Field(
        "retrieval", description="反思范围：部分/全部"
    )
    baseline: Optional[Literal["TIME", "FACT", "TIME-FACT"]] = Field(
        "TIME", description="基线：时间/事实/时间和事实"
    )

    @field_validator("chunker_strategy", mode="before")
    @classmethod
    def map_chunker_aliases_update(cls, v: str):
        if isinstance(v, str):
            m = v.strip().lower()
            alias_map = {
                "auto": "RecursiveChunker",
                "by_sentence": "SentenceChunker",
                "by_paragraph": "SemanticChunker",
                "fixed_tokens": "TokenChunker",
                "递归分块": "RecursiveChunker",
                "token 分块": "TokenChunker",
                "token分块": "TokenChunker",
                "语义分块": "SemanticChunker",
                "神经网络分块": "NeuralChunker",
                "混合分块": "HybridChunker",
                "llm 分块": "LLMChunker",
                "llm分块": "LLMChunker",
                "句子分块": "SentenceChunker",
                "延迟分块": "LateChunker",
            }
            if m in alias_map:
                return alias_map[m]
        return v


class ConfigUpdateForget(BaseModel):  # 更新遗忘引擎配置参数时使用的模型
    # 遗忘引擎配置参数更新模型
    config_id: Optional[int] = None
    lambda_time: Optional[float] = Field(0.5, ge=0.0, le=1.0, description="最低保持度，0-1 小数；默认 0.5")
    lambda_mem: Optional[float] = Field(0.5, ge=0.0, le=1.0, description="遗忘率，0-1 小数；默认 0.5")
    offset: Optional[float] = Field(0.0, ge=0.0, le=1.0, description="偏移度，0-1 小数；默认 0.0")


class ConfigPilotRun(BaseModel):  # 试运行触发请求模型
    config_id: int = Field(..., description="配置ID（唯一）")
    dialogue_text: str = Field(..., description="前端传入的对话文本，格式如 '用户: ...\nAI: ...' 可多行，试运行必填")
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ConfigFilter(BaseModel):  # 查询配置参数时使用的模型
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    config_id: Optional[int] = None
    user_id: Optional[str] = None
    apply_id: Optional[str] = None

    limit: int = Field(20, ge=1, le=200, description="返回数量上限")
    offset: int = Field(0, ge=0, description="起始偏移")


class ApiResponse(BaseModel):  # 通用API响应模型
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    code: int = Field(..., description="0=成功，非0=各类业务异常")
    msg: str = Field("", description="说明信息")
    data: Optional[Any] = Field(None, description="返回数据载荷")
    error: str = Field("", description="错误信息，失败时有值，成功为空字符串")
    time: Optional[int] = Field(None, description="响应时间（毫秒，Unix 时间戳）")


def _now_ms() -> int:
    return round(time.time() * 1000)


def ok(msg: str = "OK", data: Optional[Any] = None, time: Optional[int] = None) -> ApiResponse:
    return ApiResponse(code=0, msg=msg, data=data, error="", time=time or _now_ms())


def fail(
    msg: str,
    error_code: str = "ERROR",
    data: Optional[Any] = None,
    time: Optional[int] = None,
    query_preview: Optional[str] = None,
) -> ApiResponse:
    payload = data
    if query_preview is not None:
        if payload is None:
            payload = {"query_preview": query_preview}
        elif isinstance(payload, dict):
            payload = {**payload, "query_preview": query_preview}
        else:
            payload = {"data": payload, "query_preview": query_preview}

    return ApiResponse(
        code=1,
        msg=msg,
        data=payload,
        error=error_code,
        time=time or _now_ms(),
    )
