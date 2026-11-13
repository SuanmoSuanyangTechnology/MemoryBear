"""Model roles come from the calling operation, never from exception text."""

from enum import StrEnum

from .errors import KnowledgeError


class ModelRole(StrEnum):
    EMBEDDING = "embedding"
    RERANK = "rerank"
    LLM = "llm"
    VISION = "vision"
    IMAGE_EMBEDDING = "image_embedding"
    IMAGE_RERANK = "image_rerank"
    GRAPH_ENTITY = "graph_entity"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    VIDEO_UNDERSTANDING = "video_understanding"


class ModelFailure(StrEnum):
    CONFIG_INVALID = "CONFIG_INVALID"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    PROCESSING_FAILED = "PROCESSING_FAILED"


MODEL_FIELD_ROLES = {
    "embedding_id": ModelRole.EMBEDDING,
    "reranker_id": ModelRole.RERANK,
    "llm_id": ModelRole.LLM,
    "image2text_id": ModelRole.VISION,
    "audio2text_id": ModelRole.AUDIO_TRANSCRIPTION,
    "video2text_id": ModelRole.VIDEO_UNDERSTANDING,
}


def model_error(
    role: ModelRole,
    failure: ModelFailure,
    *,
    previous_status: int = 400,
) -> KnowledgeError:
    # 4xx→5xx 尚未批准；保留各原调用分支的 HTTP，仍发布新的业务码和提示。
    held_status = (
        previous_status
        if failure
        in {
            ModelFailure.TEMPORARILY_UNAVAILABLE,
            ModelFailure.PROCESSING_FAILED,
        }
        else None
    )
    return KnowledgeError.from_code(
        f"KB_{role.value.upper()}_MODEL_{failure.value}",
        status_code=held_status,
    )
