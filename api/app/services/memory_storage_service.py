"""
Memory Storage Service

Handles business logic for memory storage operations.
"""

import asyncio
import base64
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, PermissionDeniedError, RateLimitError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.logging_config import get_config_logger, get_logger
from app.i18n.service import t
from app.core.memory.analytics.hot_memory_tags import (
    filter_tags_with_llm,
    get_raw_tags_batch,
)
from app.core.memory.analytics.recent_activity_stats import get_recent_activity_stats
from app.core.utils.datetime_utils import to_timestamp_ms
from app.models import Workspace
from app.models.file_metadata_model import FileMetadata
from app.models.user_model import User
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.app_schema import FileType, TransferMethod
from app.schemas.memory_config_schema import ConfigurationError
from app.schemas.memory_storage_schema import (
    ConfigKey,
    ConfigParamsCreate,
    ConfigParamsDelete,
    TrialRunChatInput,
    PilotRunInput,
    ConfigUpdate,
    ConfigUpdateExtracted,
)
from app.services.memory_config_service import MemoryConfigService
from app.utils.sse_utils import format_sse_message

logger = get_logger(__name__)
config_logger = get_config_logger()


def classify_llm_error(e: Exception, prefix: str = "llm") -> tuple[str, str]:
    """
    渐进式判断错误类型，兼容多个供应商。
    判断顺序：异常类型 → HTTP 状态码 → 关键词匹配 → 兜底
    """
    # 优先级 1：异常类型（最可靠）
    if isinstance(e, AuthenticationError):
        return (f"{prefix}_key_invalid", f"{prefix} 解析失败：API Key 无效，请检查配置")
    if isinstance(e, PermissionDeniedError):
        return (f"{prefix}_key_locked", f"{prefix} 解析失败：API Key 已被锁定或权限不足")
    if isinstance(e, RateLimitError):
        return (f"{prefix}_rate_limited", f"{prefix} 解析失败：请求过于频繁，请稍后重试")
    if isinstance(e, APITimeoutError):
        return (f"{prefix}_connection_failed", f"{prefix} 解析失败：模型服务连接超时，请检查网络或 URL 配置")
    if isinstance(e, APIConnectionError):
        return (f"{prefix}_connection_failed", f"{prefix} 解析失败：模型服务连接失败，请检查 URL 配置")
    if isinstance(e, BadRequestError):
        return (f"{prefix}_bad_request", f"{prefix} 解析失败：请求参数错误，请检查文件格式或 URL 是否可访问")

    # 优先级 2：HTTP 状态码
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 400:
            return (f"{prefix}_bad_request", f"{prefix} 解析失败：请求参数错误，请检查文件格式或 URL 是否可访问")
        if status == 401:
            return (f"{prefix}_key_invalid", f"{prefix} 解析失败：API Key 无效，请检查配置")
        if status == 403:
            return (f"{prefix}_key_locked", f"{prefix} 解析失败：API Key 已被锁定或过期")
        if status in (408, 504):
            return (f"{prefix}_connection_failed", f"{prefix} 解析失败：模型服务连接超时，请检查网络或 URL 配置")
        if status == 429:
            return (f"{prefix}_rate_limited", f"{prefix} 解析失败：请求过于频繁，请稍后重试")
        if status == 404:
            return (f"{prefix}_connection_failed", f"{prefix} 解析失败：模型服务地址不存在，请检查 URL 配置")
        if status >= 500:
            return (f"{prefix}_server_error", f"{prefix} 解析失败：模型服务异常，请稍后重试")

    # 优先级 3：关键词匹配（兜底）
    error_msg = str(e).lower()
    if any(kw in error_msg for kw in ["auth", "unauthorized", "invalid_api_key", "incorrect_api_key", "401"]):
        return (f"{prefix}_key_invalid", f"{prefix} 解析失败：API Key 无效，请检查配置")
    if any(kw in error_msg for kw in ["expired", "disabled", "locked", "forbidden", "403"]):
        return (f"{prefix}_key_locked", f"{prefix} 解析失败：API Key 已被锁定或过期")
    if any(kw in error_msg for kw in ["arrearage", "欠费", "insufficient", "balance", "quota"]):
        return (f"{prefix}_key_arrearage", f"{prefix} 解析失败：账户欠费或额度不足，请充值")
    if any(kw in error_msg for kw in ["content-length", "content_length", "missing", "invalid_parameter", "400"]):
        return (f"{prefix}_bad_request", f"{prefix} 解析失败：请求参数错误，请检查文件格式或 URL 是否可访问")
    if any(kw in error_msg for kw in ["timeout", "timed out"]):
        return (f"{prefix}_connection_failed", f"{prefix} 解析失败：模型服务连接超时，请检查网络或 URL 配置")
    if any(kw in error_msg for kw in ["connect", "connection", "unreachable", "name resolution", "not found", "404"]):
        return (f"{prefix}_connection_failed", f"{prefix} 解析失败：模型服务连接失败，请检查 URL 配置")
    if any(kw in error_msg for kw in ["rate limit", "too many request", "429"]):
        return (f"{prefix}_rate_limited", f"{prefix} 解析失败：请求过于频繁，请稍后重试")
    if any(kw in error_msg for kw in ["not support", "unsupported", "capability", "modality"]):
        return (f"{prefix}_capability_mismatch", f"{prefix} 解析失败：模型不支持该能力，请检查配置")
    if any(kw in error_msg for kw in ["internal", "server error", "500", "502", "503"]):
        return (f"{prefix}_server_error", f"{prefix} 解析失败：模型服务异常，请稍后重试")

    # 优先级 4：无法识别，返回友好的默认提示（不暴露原始异常内容）
    if prefix == "llm":
        return (f"{prefix}_unknown_error", "模型调用失败，请稍后重试或联系管理员")
    return (f"{prefix}_unknown_error", f"{prefix} 解析失败，请稍后重试或联系管理员")

# Load environment variables for Neo4j connector
load_dotenv()
_neo4j_connector = Neo4jConnector(shared_driver=True)

TRIAL_RUN_CHAT_SYSTEM_PROMPT = """
You are a neutral and helpful AI assistant in a trial-run conversation. Answer
general questions normally using your own general knowledge. Use the explicit
conversation history and the supplied attachment analysis results as additional
context when they are relevant. Attachment labels identify every current-message
attachment. Answer the user's question directly, but do not reproduce the complete
attachment analyses or add an attachment-details appendix; the backend appends the
original analyses after your answer. If an attachment could not be understood, say
so instead of silently omitting it. Do not claim to have accessed long-term memory,
a private knowledge base, tools, or skills. If a question requires unavailable
private or up-to-date information, state that limitation clearly. Reply in the
language used by the user; when it is unclear, use the requested interface language.
""".strip()

TRIAL_RUN_ATTACHMENT_ANALYSIS_PROMPT = """
Analyze every supplied attachment independently and return detailed plain-text
descriptions. Preserve each attachment label exactly. For images, cover visible
objects, text, layout, and relevant context. For audio, cover speech, speakers,
important sounds, and sequence. For video, cover scenes, actions, visible text,
speech or audio, and the timeline. Do not answer the user's overall question and do
not omit an attachment. If an attachment cannot be understood, state the failure
under that attachment label.
""".strip()

TRIAL_RUN_CHAT_DEFAULT_HISTORY_ROUNDS = 20
TRIAL_RUN_CHAT_HISTORY_ROUNDS_ENV = "TRIAL_RUN_CHAT_HISTORY_ROUNDS"


class MemoryStorageService:
    """Service for memory storage operations"""

    def __init__(self):
        logger.info("MemoryStorageService initialized")

    async def get_storage_info(self) -> dict:
        """
        Example wrapper method - retrieves storage information
        
        Args:
            
        Returns:
            Storage information dictionary
        """
        logger.info("Getting storage info ")

        # Empty wrapper - implement your logic here
        result = {
            "status": "active",
            "message": "This is an example wrapper"
        }

        return result


class DataConfigService:  # 数据配置服务类（PostgreSQL）
    """Service layer for config params CRUD.

    使用 SQLAlchemy ORM 进行数据库操作。
    """

    def __init__(self, db: Optional[Session | AsyncSession] = None) -> None:
        """初始化服务

        Args:
            db: SQLAlchemy 数据库会话。
                CRUD 操作（create/update/delete/get_*）必须传入。
                pilot_run_stream 不需要传入（内部自行开短 session）。
        """
        self.db = db

    async def active(self, workspace_id: uuid.UUID, config_id: uuid.UUID, locale: str = "zh") -> Dict[str, Any]:
        stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.is_active.is_(True)
        )
        workspace = await self.db.scalar(stmt)
        if not workspace:
            raise BusinessException(t("workspace.not_found", locale=locale))
        validation_result = await MemoryConfigService(self.db).valid_config(config_id, locale=locale)
        workspace.memory_config = config_id
        await self.db.commit()
        return {
            "config_id": config_id,
            "warnings": validation_result.get("warnings", []),
            "success": True,
        }

    # --- Create ---
    async def create_async(self, params: ConfigParamsCreate) -> Dict[str, Any]:  # 创建配置参数（异步版本）
        # 业务层检查同一工作空间下是否已存在同名配置
        if params.workspace_id and params.config_name:
            existing = await MemoryConfigRepository(self.db).get_by_workspace_and_config_name_async(
                params.workspace_id, params.config_name
            )
            if existing:
                raise ValueError(f"DUPLICATE_CONFIG_NAME:{params.config_name}")

        # 如果workspace_id存在且模型字段未全部指定，则自动获取
        if params.workspace_id and not all([params.llm_id, params.embedding_id, params.rerank_id]):
            configs = await self._get_workspace_configs_async(params.workspace_id)
            if configs is None:
                raise ValueError(f"工作空间不存在: workspace_id={params.workspace_id}")

            # 只在未指定时填充（允许手动覆盖）
            if not params.llm_id:
                params.llm_id = configs.get('llm')
            if not params.embedding_id:
                params.embedding_id = configs.get('embedding')
            if not params.rerank_id:
                params.rerank_id = configs.get('rerank')
            if not params.vision_id:
                params.vision_id = configs.get('vision')
            if not params.audio_id:
                params.audio_id = configs.get('audio')
            if not params.video_id:
                params.video_id = configs.get('video')

        # reflection_model_id 和 emotion_model_id 默认与 llm_id 一致
        if not params.reflection_model_id:
            params.reflection_model_id = params.llm_id
        if not params.emotion_model_id:
            params.emotion_model_id = params.llm_id

        # 根据关联的本体场景推导 pruning_scene（语义剪枝场景与本体工程场景保持一致）
        if params.scene_id and not getattr(params, 'pruning_scene', None):
            params.pruning_scene = await self._resolve_pruning_scene_from_scene_id(params.scene_id)

        config = await MemoryConfigRepository(self.db).create_async(params)
        await self.db.commit()
        return {"affected": 1, "config_id": config.config_id}

    def _get_workspace_configs(self, workspace_id) -> Optional[Dict[str, Any]]:
        """获取工作空间模型配置（内部方法，便于测试）"""
        from app.db import get_db_read
        from app.repositories.workspace_repository import get_workspace_models_configs

        with get_db_read() as db_session:
            return get_workspace_models_configs(db_session, workspace_id)

    async def _get_workspace_configs_async(self, workspace_id) -> Optional[Dict[str, Any]]:
        """Async version of _get_workspace_configs — uses self.db (AsyncSession) directly."""
        from app.repositories.workspace_repository import WorkspaceRepository
        return await WorkspaceRepository(self.db).get_workspace_models_configs_async(workspace_id)

    async def _resolve_pruning_scene_from_scene_id(self, scene_id) -> Optional[str]:
        """根据本体场景ID获取对应的 scene_name，作为语义剪枝场景值

        Args:
            scene_id: 本体场景UUID

        Returns:
            scene_name 字符串，查询失败时返回 None
        """
        try:
            from app.repositories.ontology_scene_repository import OntologySceneRepository
            scene = await OntologySceneRepository(self.db).get_by_id_async(scene_id)
            return scene.scene_name if scene else None
        except Exception as e:
            logger.warning(f"_resolve_pruning_scene_from_scene_id failed for scene_id={scene_id}: {e}", exc_info=True)
            return None

    # --- Delete ---
    async def delete_async(self, key: ConfigParamsDelete) -> Dict[str, Any]:  # 删除配置参数（异步版本）
        success = await MemoryConfigRepository(self.db).delete_async(key.config_id)
        if not success:
            raise ValueError("未找到配置")
        return {"affected": 1}

    # --- Update ---
    async def update_async(self, update: ConfigUpdate) -> Dict[str, Any]:  # 部分更新配置参数（异步版本）
        config = await MemoryConfigRepository(self.db).update_async(update)
        if not config:
            raise ValueError("未找到配置")
        return {"affected": 1}

    async def update_extracted_async(self, update: ConfigUpdateExtracted) -> Dict[str, Any]:  # 更新记忆萃取引擎配置参数（异步版本）
        config = await MemoryConfigRepository(self.db).update_extracted_async(update)
        if not config:
            raise ValueError("未找到配置")
        return {"affected": 1}

    # --- Forget config params ---
    # 遗忘引擎配置方法已迁移到 memory_forget_service.py
    # 使用新方法: MemoryForgetService.read_forgetting_config() 和 MemoryForgetService.update_forgetting_config()

    # --- Read ---
    async def get_extracted_async(self, key: ConfigKey) -> Dict[str, Any]:  # 获取萃取配置参数（异步版本）
        result = await MemoryConfigRepository(self.db).get_extracted_config_async(key.config_id)
        if not result:
            raise ValueError("未找到配置")
        return result

    # --- Read All ---
    async def get_all_async(self, workspace_id) -> List[Dict[str, Any]]:  # 获取所有配置参数（异步版本）
        results = await MemoryConfigRepository(self.db).get_all_async(workspace_id)

        # 检查并修正 pruning_scene 与 scene_name 不一致的记录
        needs_commit = False
        for config, scene_name in results:
            if scene_name and config.pruning_scene != scene_name:
                logger.info(
                    f"修正 pruning_scene: config_id={config.config_id} "
                    f"'{config.pruning_scene}' -> '{scene_name}'"
                )
                config.pruning_scene = scene_name
                needs_commit = True
        if needs_commit:
            await self.db.commit()

        try:
            activate_config_id = await MemoryConfigService(self.db).get_workspace_active_config_id_async(workspace_id)
        except Exception:
            activate_config_id = None

        # 将 ORM 对象转换为字典列表，时间字段统一转为 UTC 毫秒时间戳
        data_list = []
        for config, scene_name in results:
            # 安全地转换 user_id 为 int
            config_id_old = None
            if config.config_id_old:
                try:
                    config_id_old = int(config.config_id_old)
                except (ValueError, TypeError):
                    config_id_old = None

            config_dict = {
                "config_id": str(config.config_id),
                "config_name": config.config_name,
                "config_desc": config.config_desc,
                "workspace_id": str(config.workspace_id) if config.workspace_id else None,
                "end_user_id": config.end_user_id,
                "config_id_old": config_id_old,
                "apply_id": config.apply_id,
                "scene_id": str(config.scene_id) if config.scene_id else None,
                "scene_name": scene_name,  # 新增：场景名称
                "is_system_default": config.is_default,  # 是否为系统默认配置
                "llm_id": config.llm_id,
                "embedding_id": config.embedding_id,
                "rerank_id": config.rerank_id,
                "enable_llm_dedup_blockwise": config.enable_llm_dedup_blockwise,
                "enable_llm_disambiguation": config.enable_llm_disambiguation,
                "deep_retrieval": config.deep_retrieval,
                "t_type_strict": config.t_type_strict,
                "t_name_strict": config.t_name_strict,
                "t_overall": config.t_overall,
                "state": config.state,
                "chunker_strategy": config.chunker_strategy,
                "pruning_enabled": config.pruning_enabled,
                "pruning_scene": config.pruning_scene,
                "pruning_threshold": config.pruning_threshold,
                "enable_self_reflexion": config.enable_self_reflexion,
                "iteration_period": config.iteration_period,
                "reflexion_range": config.reflexion_range,
                "baseline": config.baseline,
                "statement_granularity": config.statement_granularity,
                "include_dialogue_context": config.include_dialogue_context,
                "max_context": config.max_context,
                "lambda_time": config.lambda_time,
                "lambda_mem": config.lambda_mem,
                "offset": config.offset,
                "created_at": to_timestamp_ms(config.created_at),
                "updated_at": to_timestamp_ms(config.updated_at),
                "is_active": str(config.config_id) == str(activate_config_id),
            }
            data_list.append(config_dict)

        return data_list

    @staticmethod
    def _trial_run_media_model_id(memory_config, file_type: FileType) -> uuid.UUID:
        """返回某一媒体类型的专用模型，未配置时回退文本模型。"""
        if file_type == FileType.IMAGE:
            return memory_config.vision_model_id or memory_config.llm_model_id
        if file_type == FileType.AUDIO:
            return memory_config.audio_model_id or memory_config.llm_model_id
        if file_type == FileType.VIDEO:
            return memory_config.video_model_id or memory_config.llm_model_id
        return memory_config.llm_model_id

    @staticmethod
    def _trial_run_history_round_limit() -> int:
        """返回试运行对话保留的最新用户轮次数。"""
        raw_limit = os.getenv(
            TRIAL_RUN_CHAT_HISTORY_ROUNDS_ENV,
            str(TRIAL_RUN_CHAT_DEFAULT_HISTORY_ROUNDS),
        )
        try:
            return max(0, int(raw_limit))
        except (TypeError, ValueError):
            logger.warning(
                "Invalid %s=%r; falling back to %s",
                TRIAL_RUN_CHAT_HISTORY_ROUNDS_ENV,
                raw_limit,
                TRIAL_RUN_CHAT_DEFAULT_HISTORY_ROUNDS,
            )
            return TRIAL_RUN_CHAT_DEFAULT_HISTORY_ROUNDS

    @staticmethod
    def _latest_trial_run_history(history: list[Any], max_rounds: int) -> list[Any]:
        """以 user 消息为一轮的起点，保留最新 max_rounds 轮及其后续回复。"""
        if max_rounds <= 0:
            return []

        user_rounds = 0
        for index in range(len(history) - 1, -1, -1):
            if history[index].role != "user":
                continue
            user_rounds += 1
            if user_rounds == max_rounds:
                return list(history[index:])
        return list(history)

    @staticmethod
    def _normalize_capabilities(capabilities) -> set[str]:
        return {
            str(getattr(capability, "value", capability)).lower()
            for capability in capabilities or []
        }

    @staticmethod
    def _unsupported_file_part(file_type: FileType, language: str) -> dict[str, str]:
        labels = {
            "zh": {
                FileType.IMAGE: "图片",
                FileType.AUDIO: "音频",
                FileType.VIDEO: "视频",
            },
            "en": {
                FileType.IMAGE: "image",
                FileType.AUDIO: "audio",
                FileType.VIDEO: "video",
            },
        }
        locale = "en" if language == "en" else "zh"
        label = labels[locale].get(file_type, str(file_type))
        if locale == "en":
            text = f"[The selected model does not support this {label} attachment.]"
        else:
            text = f"[当前选中的模型不支持该{label}附件。]"
        return {"type": "text", "text": text}

    @staticmethod
    def _local_file_error_part(language: str) -> dict[str, str]:
        if language == "en":
            text = "[The local attachment could not be loaded.]"
        else:
            text = "[本地附件加载失败。]"
        return {"type": "text", "text": text}

    @staticmethod
    def _file_marker_part(file, position: int, language: str) -> dict[str, str]:
        """为每个附件添加稳定的顺序和类型标记，避免模型静默忽略媒体块。"""
        file_type = str(getattr(file.type, "value", file.type))
        file_name = file.name or str(file.upload_file_id or file.url or "")
        if language == "en":
            text = f"[Attachment {position}: type={file_type}, name={file_name}]"
        else:
            text = f"[附件 {position}：类型={file_type}，名称={file_name}]"
        return {"type": "text", "text": text}

    @staticmethod
    def _replace_local_media_url(
            parts: list[dict[str, Any]],
            content: bytes,
            mime_type: str,
    ) -> None:
        """将 URL 型多模态 part 中的本地地址替换为 Base64 Data URL。"""
        data_url: str | None = None

        def get_data_url() -> str:
            nonlocal data_url
            if data_url is None:
                encoded = base64.b64encode(content).decode("ascii")
                data_url = f"data:{mime_type};base64,{encoded}"
            return data_url

        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "image_url" and isinstance(part.get("image_url"), dict):
                part["image_url"]["url"] = get_data_url()
            elif part_type == "image" and isinstance(part.get("image"), str):
                part["image"] = get_data_url()
            elif part_type == "audio_url" and isinstance(part.get("audio_url"), dict):
                part["audio_url"]["url"] = get_data_url()
            elif part_type == "audio" and isinstance(part.get("audio"), str):
                part["audio"] = get_data_url()
            elif part_type == "video_url" and isinstance(part.get("video_url"), dict):
                part["video_url"]["url"] = get_data_url()
            elif part_type == "video" and isinstance(part.get("video"), str):
                part["video"] = get_data_url()

    @staticmethod
    async def _load_trial_run_local_file(
            file,
            workspace_id: uuid.UUID,
            tenant_id: uuid.UUID,
    ) -> tuple[bytes, str]:
        """校验本地附件归属并从配置的存储后端读取原始字节。"""
        import magic
        from app.db import get_async_db_context
        from app.services.file_storage_service import FileStorageService

        async with get_async_db_context() as db:
            metadata = await db.scalar(
                select(FileMetadata).where(
                    FileMetadata.id == file.upload_file_id,
                    FileMetadata.status == "completed",
                    FileMetadata.workspace_id == workspace_id,
                    FileMetadata.tenant_id == tenant_id,
                )
            )
            if metadata is None:
                raise ValueError("local attachment is unavailable in the current workspace")
            file_key = metadata.file_key
            metadata_content_type = metadata.content_type
            metadata_file_ext = metadata.file_ext
            metadata_file_name = metadata.file_name

        content = await FileStorageService().download_file(file_key)
        if not content:
            raise ValueError("local attachment is empty")

        detected_mime = magic.from_buffer(content, mime=True)
        mime_type = detected_mime or metadata_content_type or "application/octet-stream"
        file.set_content(content)
        file.url = file.url or f"local-file://{file.upload_file_id}"
        if file.type == FileType.AUDIO and metadata_file_ext:
            audio_ext = metadata_file_ext.lstrip(".").lower()
            file.file_type = f"audio/{audio_ext}"
        else:
            file.file_type = mime_type
        if not file.name:
            file.name = metadata_file_name
        return content, mime_type

    @classmethod
    async def _process_trial_run_files(
            cls,
            files,
            multimodal_service,
            workspace_id: uuid.UUID,
            tenant_id: uuid.UUID,
            capabilities,
            language: str,
    ) -> list[dict[str, Any]]:
        """按请求顺序处理附件，对模型不支持的类型生成可见文本提示。"""
        normalized_capabilities = cls._normalize_capabilities(capabilities)
        required_capabilities = {
            FileType.IMAGE: "vision",
            FileType.AUDIO: "audio",
            FileType.VIDEO: "video",
        }
        processed_parts: list[dict[str, Any]] = []
        for position, file in enumerate(files or [], start=1):
            required = required_capabilities.get(file.type)
            if required and required not in normalized_capabilities:
                processed_parts.append(cls._file_marker_part(file, position, language))
                processed_parts.append(cls._unsupported_file_part(file.type, language))
                continue

            local_media: tuple[bytes, str] | None = None
            if file.transfer_method == TransferMethod.LOCAL_FILE:
                try:
                    content, mime_type = await cls._load_trial_run_local_file(
                        file,
                        workspace_id,
                        tenant_id,
                    )
                    if file.type in {FileType.IMAGE, FileType.AUDIO, FileType.VIDEO}:
                        local_media = (content, mime_type)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error(
                        "[TRIAL_RUN_CHAT_STREAM] Failed to load local attachment: file_id=%s",
                        file.upload_file_id,
                        exc_info=True,
                    )
                    processed_parts.append(cls._file_marker_part(file, position, language))
                    processed_parts.append(cls._local_file_error_part(language))
                    continue

            parts = await multimodal_service.process_files(
                [file],
                workspace_id=workspace_id,
                document_image_recognition=False,
                include_processing_errors=True,
            )
            if local_media:
                cls._replace_local_media_url(parts, *local_media)
            processed_parts.append(cls._file_marker_part(file, position, language))
            processed_parts.extend(parts)
        return processed_parts

    @staticmethod
    def _stream_content_to_texts(content: Any) -> list[str]:
        """从不同 provider 的流式 content 形态中提取文本块。"""
        if isinstance(content, str):
            return [content] if content else []
        if isinstance(content, dict):
            text = content.get("text")
            return [str(text)] if text else []
        if not isinstance(content, list):
            return []

        texts: list[str] = []
        for item in content:
            if isinstance(item, str) and item:
                texts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    texts.append(str(text))
        return texts

    @staticmethod
    def _extract_message_from_event_output(output: Any) -> Any | None:
        if output is None:
            return None
        if getattr(output, "usage_metadata", None) or getattr(output, "response_metadata", None):
            return output
        if isinstance(output, dict):
            value = output.get("message") or output.get("output")
            if value is not None and not isinstance(value, (dict, list, str)):
                return value
            generations = output.get("generations")
            if isinstance(generations, list):
                for generation_group in generations:
                    items = generation_group if isinstance(generation_group, list) else [generation_group]
                    for generation in items:
                        message = getattr(generation, "message", None)
                        if message is not None:
                            return message
        return getattr(output, "message", None)

    @staticmethod
    def _extract_total_tokens(message: Any) -> int:
        if message is None:
            return 0
        response_metadata = getattr(message, "response_metadata", None)
        if isinstance(response_metadata, dict):
            usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
            if isinstance(usage, dict) and usage.get("total_tokens"):
                return int(usage["total_tokens"])
        usage_metadata = getattr(message, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            return int(usage_metadata.get("total_tokens") or 0)
        return int(getattr(usage_metadata, "total_tokens", 0) or 0)

    @staticmethod
    def _trial_run_chat_error_data(language: str, kind: str = "generation") -> dict[str, Any]:
        english = language == "en"
        if kind == "model":
            message = "Model unavailable" if english else "模型不可用"
            error = "no available api key"
        elif kind == "access":
            message = "Configuration access denied" if english else "无权访问该配置"
            error = "configuration access denied"
        else:
            message = "Chat generation failed" if english else "对话生成失败"
            error = "trial run chat generation failed"
        return {"code": 5000, "message": message, "error": error}

    async def trial_run_chat_stream(
            self,
            payload: TrialRunChatInput,
            language: str = "zh",
            workspace_id: uuid.UUID | None = None,
            tenant_id: uuid.UUID | None = None,
    ) -> AsyncGenerator[str, None]:
        """按附件类型分别解析，再由文本模型合并生成流式回答。"""
        from app.core.models import RedBearLLM, RedBearModelConfig
        from app.db import get_async_db_context
        from app.models.models_model import ModelType
        from app.schemas.model_schema import ModelInfo
        from app.services.model_service import ModelApiKeyService
        from app.services.multimodal_service import MultimodalService
        from langchain.agents import create_agent
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        start_time = time.perf_counter()

        attachment_groups: dict[FileType, list[dict[str, Any]]] = {
            FileType.IMAGE: [],
            FileType.AUDIO: [],
            FileType.VIDEO: [],
            FileType.DOCUMENT: [],
        }
        attachment_type_counts = {file_type: 0 for file_type in attachment_groups}
        for file_index, file in enumerate(payload.files):
            attachment_type_counts[file.type] += 1
            attachment_groups[file.type].append({
                "file_index": file_index,
                "type_index": attachment_type_counts[file.type],
                "file": file,
            })

        def attachment_label(ref: dict[str, Any]) -> str:
            file = ref["file"]
            file_type = str(getattr(file.type, "value", file.type))
            name = file.name or str(file.upload_file_id or file.url or "")
            type_labels_zh = {
                FileType.IMAGE: "图片文件",
                FileType.AUDIO: "音频文件",
                FileType.VIDEO: "视频文件",
                FileType.DOCUMENT: "文档文件",
            }
            location_zh = f"{type_labels_zh[file.type]} {ref['type_index']}"
            location_en = f"{file_type} file {ref['type_index']}"
            if language == "en":
                return f"[Details of {location_en}: name={name}]"
            return f"[{location_zh}的详细内容：名称={name}]"

        def group_failure_text(refs: list[dict[str, Any]], reason: str) -> str:
            return "\n".join(f"{attachment_label(ref)}\n{reason}" for ref in refs)

        def snapshot_runtime(api_key_obj) -> tuple[ModelInfo, uuid.UUID]:
            return (
                ModelInfo(
                    model_name=api_key_obj.model_name,
                    provider=api_key_obj.provider,
                    api_key=api_key_obj.api_key,
                    api_base=api_key_obj.api_base or "",
                    is_omni=api_key_obj.is_omni,
                    model_type=ModelType.LLM,
                    capability=api_key_obj.capability or [],
                ),
                api_key_obj.id,
            )

        def build_llm(model_info: ModelInfo, *, streaming: bool, max_tokens: int) -> RedBearLLM:
            return RedBearLLM(
                RedBearModelConfig(
                    model_name=model_info.model_name,
                    provider=model_info.provider,
                    api_key=model_info.api_key,
                    base_url=model_info.api_base,
                    is_omni=model_info.is_omni,
                    capability=model_info.capability,
                    extra_params={
                        "temperature": 0.2 if not streaming else 0.7,
                        "max_tokens": max_tokens,
                        "streaming": streaming,
                    },
                ),
                type=ModelType.CHAT,
            )

        try:
            async with get_async_db_context() as db:
                memory_config = await MemoryConfigService(db).load_memory_config_async(payload.config_id)
                if (
                        workspace_id is None
                        or tenant_id is None
                        or memory_config.workspace_id != workspace_id
                        or memory_config.tenant_id != tenant_id
                ):
                    logger.warning(
                        "Trial-run chat configuration access denied: config_id=%s, config_workspace=%s, request_workspace=%s",
                        payload.config_id,
                        memory_config.workspace_id,
                        workspace_id,
                    )
                    yield format_sse_message("error", self._trial_run_chat_error_data(language, "access"))
                    return

                runtime_cache: dict[uuid.UUID, tuple[ModelInfo, uuid.UUID] | None] = {}

                async def get_runtime(model_config_id: uuid.UUID):
                    if model_config_id not in runtime_cache:
                        api_key_obj = await ModelApiKeyService.get_available_api_key_bridge_async(
                            db,
                            model_config_id,
                            tenant_id=memory_config.tenant_id,
                        )
                        runtime_cache[model_config_id] = (
                            snapshot_runtime(api_key_obj) if api_key_obj else None
                        )
                    return runtime_cache[model_config_id]

                final_runtime = await get_runtime(memory_config.llm_model_id)
                if final_runtime is None:
                    yield format_sse_message("error", self._trial_run_chat_error_data(language, "model"))
                    return

                media_runtimes: dict[FileType, tuple[ModelInfo, uuid.UUID] | None] = {}
                required_capabilities = {
                    FileType.IMAGE: "vision",
                    FileType.AUDIO: "audio",
                    FileType.VIDEO: "video",
                }
                for file_type in (FileType.IMAGE, FileType.AUDIO, FileType.VIDEO):
                    if not attachment_groups[file_type]:
                        continue
                    model_config_id = self._trial_run_media_model_id(memory_config, file_type)
                    runtime = await get_runtime(model_config_id)
                    required = required_capabilities[file_type]
                    if runtime is not None and required not in self._normalize_capabilities(runtime[0].capability):
                        runtime = None
                    if runtime is None and model_config_id != memory_config.llm_model_id:
                        fallback = final_runtime
                        if required in self._normalize_capabilities(fallback[0].capability):
                            runtime = fallback
                    media_runtimes[file_type] = runtime

                config_workspace_id = memory_config.workspace_id
                config_tenant_id = memory_config.tenant_id

            final_model_info, final_api_key_id = final_runtime

            yield format_sse_message("start", {
                "conversation_id": "",
                "message_id": str(uuid.uuid4()),
                "user_message_id": str(uuid.uuid4()),
            })

            async def analyze_media_group(file_type: FileType) -> dict[str, Any]:
                refs = attachment_groups[file_type]
                runtime = media_runtimes.get(file_type)
                if runtime is None:
                    # 返回错误标记
                    return {
                        "error": True,
                        "type": file_type.value.upper(),
                        "error_field": f"{file_type.value.upper()}_model_unavailable",
                        "message": f"{file_type.value.upper()} 附件：没有可用的处理模型",
                    }

                model_info, api_key_id = runtime
                try:
                    # 本地文件已由短会话加载，远程文件已有 URL；后续格式化不需要数据库。
                    multimodal_service = MultimodalService(None, model_info)
                    parts = await self._process_trial_run_files(
                        [ref["file"] for ref in refs],
                        multimodal_service,
                        config_workspace_id,
                        config_tenant_id,
                        model_info.capability,
                        language,
                    )

                    mapping = "\n".join(
                        f"Attachment {index} = {attachment_label(ref)}"
                        for index, ref in enumerate(refs, start=1)
                    )
                    requested_language = "English" if language == "en" else "Chinese"
                    parser_prompt = (
                        f"{TRIAL_RUN_ATTACHMENT_ANALYSIS_PROMPT}\n"
                        f"Reply in {requested_language}.\n{mapping}"
                    )
                    parser_llm = build_llm(model_info, streaming=False, max_tokens=2000)
                    response = await parser_llm.ainvoke([
                        SystemMessage(content=parser_prompt),
                        HumanMessage(content=parts),
                    ])
                    response_text = "".join(
                        self._stream_content_to_texts(getattr(response, "content", None))
                    ).strip()
                    if not response_text:
                        # 返回空文本也视为错误
                        return {
                            "error": True,
                            "type": file_type.value,
                            "error_field": f"{file_type.value}_empty_response",
                            "message": f"{file_type.value} 附件解析未返回文本",
                        }
                    return {
                        "text": response_text,
                        "tokens": self._extract_total_tokens(response),
                        "api_key_id": api_key_id,
                    }
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "[TRIAL_RUN_CHAT_STREAM] %s attachment analysis failed",
                        file_type.value,
                        exc_info=True,
                    )
                    # 使用错误分类，前缀使用大写
                    error_field, message = classify_llm_error(e, prefix=file_type.value.upper())
                    return {
                        "error": True,
                        "type": file_type.value.upper(),
                        "error_field": error_field,
                        "message": message,
                    }

            async def extract_document_group() -> dict[str, Any]:
                refs = attachment_groups[FileType.DOCUMENT]
                documents: list[str] = []
                # 文档字节在短会话关闭后下载和解析，避免长时间占用连接。
                multimodal_service = MultimodalService(None, final_model_info)
                for ref in refs:
                    file = ref["file"]
                    try:
                        if file.transfer_method == TransferMethod.LOCAL_FILE:
                            await self._load_trial_run_local_file(
                                file,
                                config_workspace_id,
                                config_tenant_id,
                            )
                        text = await multimodal_service.extract_document_text(file)
                        documents.append(f"{attachment_label(ref)}\n{text}")
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(
                            "[TRIAL_RUN_CHAT_STREAM] Document extraction failed",
                            exc_info=True,
                        )
                        # 使用错误分类
                        error_field, message = classify_llm_error(e, prefix="DOCUMENT")
                        return {
                            "error": True,
                            "type": "DOCUMENT",
                            "error_field": error_field,
                            "message": message,
                        }
                return {"text": "\n\n".join(documents), "tokens": 0, "api_key_id": None}

            analysis_tasks = [
                analyze_media_group(file_type)
                for file_type in (FileType.IMAGE, FileType.AUDIO, FileType.VIDEO)
                if attachment_groups[file_type]
            ]
            if attachment_groups[FileType.DOCUMENT]:
                analysis_tasks.append(extract_document_group())

            analysis_results = await asyncio.gather(*analysis_tasks) if analysis_tasks else []

            # 收集所有错误
            error_messages = []
            first_error_field = "attachment_parse_failed"
            for result in analysis_results:
                if isinstance(result, dict) and result.get("error"):
                    error_messages.append(result["message"])
                    if first_error_field == "attachment_parse_failed":
                        first_error_field = result["error_field"]

            # 如果有错误，一次性返回，所有信息合并到 message 中
            if error_messages:
                combined_message = "附件解析失败：" + "；".join(error_messages)
                yield format_sse_message("error", {
                    "code": 5000,
                    "error": first_error_field,
                    "message": combined_message,
                })
                return

            # 继续原有逻辑
            attachment_context = "\n\n".join(
                result["text"] for result in analysis_results if result.get("text")
            )
            total_tokens = sum(int(result.get("tokens") or 0) for result in analysis_results)
            usage_api_key_ids = [
                result["api_key_id"]
                for result in analysis_results
                if result.get("api_key_id") is not None
            ]

            history_round_limit = self._trial_run_history_round_limit()
            limited_history = self._latest_trial_run_history(
                payload.history,
                history_round_limit,
            )
            if len(limited_history) < len(payload.history):
                logger.info(
                    "[TRIAL_RUN_CHAT_STREAM] History truncated: original_messages=%s, retained_messages=%s, max_rounds=%s",
                    len(payload.history),
                    len(limited_history),
                    history_round_limit,
                )

            messages: list[Any] = []
            for history_message in limited_history:
                if history_message.role == "user":
                    messages.append(HumanMessage(content=history_message.content))
                else:
                    messages.append(AIMessage(content=history_message.content))

            current_content = payload.message
            if attachment_context:
                current_content += (
                    "\n\n<attachment_analysis_results>\n"
                    f"{attachment_context}\n"
                    "</attachment_analysis_results>"
                )
            messages.append(HumanMessage(content=current_content))

            final_llm = build_llm(final_model_info, streaming=True, max_tokens=2000)
            requested_language = "English" if language == "en" else "Chinese"
            system_prompt = f"{TRIAL_RUN_CHAT_SYSTEM_PROMPT}\nThe requested interface language is {requested_language}."
            agent = create_agent(model=final_llm, tools=[], system_prompt=system_prompt)

            full_content = ""
            final_tokens = 0
            async for event in agent.astream_events({"messages": messages}, version="v2"):
                event_type = event.get("event")
                if event_type == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    for text_chunk in self._stream_content_to_texts(getattr(chunk, "content", None)):
                        full_content += text_chunk
                        yield format_sse_message("message", {"content": text_chunk})
                elif event_type == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    message = self._extract_message_from_event_output(output)
                    final_tokens = max(final_tokens, self._extract_total_tokens(message))

            if attachment_context:
                details_title = (
                    "Current attachment details"
                    if language == "en"
                    else "当前附件详细信息"
                )
                attachment_details = (
                    f"\n\n---\n\n## {details_title}\n\n"
                    f"{attachment_context}"
                )
                for offset in range(0, len(attachment_details), 2000):
                    details_chunk = attachment_details[offset:offset + 2000]
                    full_content += details_chunk
                    yield format_sse_message("message", {"content": details_chunk})

            total_tokens += final_tokens
            usage_api_key_ids.append(final_api_key_id)

            try:
                async with get_async_db_context() as db:
                    for api_key_id in usage_api_key_ids:
                        await ModelApiKeyService.record_api_key_usage_bridge_async(db, api_key_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Usage accounting is observability metadata. A write failure here must
                # not turn an already completed model response into an SSE error event.
                logger.error("[TRIAL_RUN_CHAT_STREAM] Failed to record API key usage", exc_info=True)

            yield format_sse_message("end", {
                "elapsed_time": time.perf_counter() - start_time,
                "message_length": len(full_content),
                "usage": {"total_tokens": total_tokens},
            })
        except asyncio.CancelledError:
            logger.info("[TRIAL_RUN_CHAT_STREAM] Client disconnected during streaming")
            raise
        except Exception as e:
            logger.error("[TRIAL_RUN_CHAT_STREAM] Error during streaming", exc_info=True)
            error_msg = str(e).lower()
            # 区分配置错误和 LLM 错误
            if "configuration not found" in error_msg or "config" in error_msg and "not found" in error_msg:
                yield format_sse_message("error", {
                    "code": 5000,
                    "error": "config_error",
                    "message": "配置读取失败",
                })
            else:
                # 使用错误分类
                error_field, message = classify_llm_error(e, prefix="llm")
                yield format_sse_message("error", {
                    "code": 5000,
                    "error": error_field,
                    "message": message,
                })

    async def pilot_run_stream(self, payload: PilotRunInput, language: str = "zh") -> AsyncGenerator[str, None]:
        """
        流式执行试运行，产生 SSE 格式的进度事件

        db session 生命周期策略：
        - 阶段1（短 session）：用 get_db_read() 查配置、初始化 llm_client，with 块结束立即归还连接
        - 阶段2（无 session）：LLM 调用可能耗时数十秒，完全不持有 db 连接
        - 本方法不依赖 FastAPI Depends(get_db)，自行管理 session 生命周期

        Args:
            payload: 试运行配置和 QA 格式消息列表（v0.3.13：config_id + messages）
            language: 语言类型 ("zh" 中文, "en" 英文)，默认中文

        Yields:
            SSE 格式的字符串，包含以下事件类型：
            - 各种阶段名称: 进度更新 (如 starting, knowledge_extraction_complete 等)
            - result: 最终结果
            - error: 错误信息
            - done: 完成标记

        Raises:
            ValueError: 当配置无效或参数缺失时
            RuntimeError: 当管线执行失败时
        """
        from pathlib import Path
        from app.db import get_db_read
        from app.core.memory.pipelines.base_pipeline import ModelClientMixin

        project_root = str(Path(__file__).resolve().parents[2])

        try:
            # 发出初始进度事件
            yield format_sse_message("starting", {
                "message": "开始试运行...",
                "time": int(time.time() * 1000)
            })

            # ── 阶段1：短 session，只读查询，with 块结束立即归还连接 ──────────
            payload_cid = str(getattr(payload, "config_id", "") or "").strip()
            cid: Optional[str] = payload_cid if payload_cid else None

            if not cid:
                raise ValueError("未提供 payload.config_id，禁止启动试运行")

            # v0.3.13: 直接透传 messages，不再解析 dialogue_text / custom_text
            messages = payload.messages
            if not messages:
                raise ValueError("试运行模式必须提供至少一条 message")

            with get_db_read() as db:
                # 1a. 加载记忆配置
                try:
                    config_service = MemoryConfigService(db)
                    memory_config = config_service.load_memory_config(
                        config_id=str(cid)
                    )
                    logger.info(f"Configuration loaded successfully: {memory_config.config_name}")
                except ConfigurationError as e:
                    raise RuntimeError(f"Configuration loading failed: {e}")

                logger.info(
                    f"[PILOT_RUN_STREAM] messages count={len(messages)}, "
                    f"scene_id={memory_config.scene_id}"
                )

                # 1c. 初始化 LLM 客户端（只需查一次模型配置，之后 llm_client 是独立对象）
                try:
                    llm_client = ModelClientMixin.get_llm_client(db, memory_config.llm_model_id, memory_config.tenant_id)
                    logger.info("[PILOT_RUN_STREAM] LLM client initialized")
                except Exception as e:
                    raise RuntimeError(f"LLM client initialization failed: {e}")
            # ── with 块结束，db 连接立即归还连接池 ───────────────────────────
            logger.info("[PILOT_RUN_STREAM] db session closed, starting LLM pipeline")

            # ── 阶段2：无 session，LLM 管线执行 ──────────────────────────────
            # 使用队列在回调和生成器之间传递进度事件
            progress_queue: asyncio.Queue = asyncio.Queue()

            async def progress_callback(stage: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
                """
                进度回调函数，将进度事件放入队列

                Args:
                    stage: 阶段标识
                    message: 进度消息
                    data: 可选的结果数据（用于传递节点执行结果）
                """
                await progress_queue.put((stage, message, data))

            # 步骤 3: 在后台任务中执行管线（无 db 依赖）
            async def run_pipeline():
                """在后台执行管线并捕获异常"""
                try:
                    from app.services.pilot_run_service import run_pilot_extraction

                    logger.info(
                        f"[PILOT_RUN_STREAM] Calling run_pilot_extraction with messages count: {len(messages)}"
                    )
                    await run_pilot_extraction(
                        memory_config=memory_config,
                        messages=messages,
                        llm_client=llm_client,
                        progress_callback=progress_callback,
                        language=language,
                    )
                    logger.info("[PILOT_RUN_STREAM] pipeline_main completed")

                    # 标记管线完成
                    await progress_queue.put(("__PIPELINE_COMPLETE__", "", None))
                except Exception as e:
                    # 将异常放入队列
                    await progress_queue.put(("__PIPELINE_ERROR__", str(e), None))

            # 启动后台任务
            pipeline_task = asyncio.create_task(run_pipeline())

            # 步骤 4: 从队列中读取进度事件并发出
            while True:
                try:
                    # 等待进度事件，设置超时以检测客户端断开
                    stage, message, data = await asyncio.wait_for(
                        progress_queue.get(),
                        timeout=0.5
                    )

                    # 检查特殊标记
                    if stage == "__PIPELINE_COMPLETE__":
                        break
                    elif stage == "__PIPELINE_ERROR__":
                        raise RuntimeError(message)

                    # 构建进度事件数据
                    progress_data = {
                        "message": message,
                        "time": int(time.time() * 1000)
                    }

                    # 如果有结果数据，添加到事件中
                    if data:
                        progress_data["data"] = data

                    # 发出进度事件，使用 stage 作为事件类型
                    yield format_sse_message(stage, progress_data)

                except TimeoutError:
                    # 超时，继续等待（这允许检测客户端断开）
                    continue

            # 等待管线任务完成
            await pipeline_task

            # 步骤 5: 读取提取结果
            from app.core.config import settings
            result_path = settings.get_memory_output_path("extracted_result.json")
            if not os.path.isfile(result_path):
                raise FileNotFoundError(f"试运行完成，但未找到提取结果文件: {result_path}")

            with open(result_path, "r", encoding="utf-8") as rf:
                extracted_result = json.load(rf)

            # 步骤 6: 组装结果（试运行不做额外覆盖率后处理）
            result_data = {
                "config_id": cid,
                "time_log": os.path.join(project_root, "logs", "time.log"),
                "extracted_result": extracted_result,
            }

            yield format_sse_message("result", result_data)

            # 步骤 7: 发出完成事件
            yield format_sse_message("done", {
                "message": "试运行完成",
                "time": int(time.time() * 1000)
            })

        except asyncio.CancelledError:
            # 客户端断开连接
            logger.info("[PILOT_RUN_STREAM] Client disconnected during streaming")
            raise
        except Exception as e:
            # 发出错误事件
            logger.error(f"[PILOT_RUN_STREAM] Error during streaming: {e}", exc_info=True)
            yield format_sse_message("error", {
                "code": 5000,
                "message": "试运行失败",
                "error": str(e),
                "time": int(time.time() * 1000)
            })


# -------------------- Neo4j Search & Analytics (fused from data_search_service.py) --------------------
# Ensure env for connector (e.g., NEO4J_PASSWORD)


async def search_dialogue(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_DIALOGUE,
        end_user_id=end_user_id,
    )
    data = {"search_for": "dialogue", "num": result[0]["num"]}
    return data


async def search_chunk(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_CHUNK,
        end_user_id=end_user_id,
    )
    data = {"search_for": "chunk", "num": result[0]["num"]}
    return data


async def search_statement(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_STATEMENT,
        end_user_id=end_user_id,
    )
    data = {"search_for": "statement", "num": result[0]["num"]}
    return data


async def search_entity(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_ENTITY,
        end_user_id=end_user_id,
    )
    data = {"search_for": "entity", "num": result[0]["num"]}
    return data


async def kb_type_distribution(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """统一知识库类型分布接口。

    聚合 dialogue/chunk/statement/entity 四类计数，返回统一的分布结构，便于前端一次性消费。
    """
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_ALL,
        end_user_id=end_user_id,
    )

    # 检查结果是否为空或长度不足
    if not result or len(result) < 4:
        data = {
            "total": 0,
            "distribution": [
                {"type": "dialogue", "count": 0},
                {"type": "chunk", "count": 0},
                {"type": "statement", "count": 0},
                {"type": "entity", "count": 0},
            ]
        }
        return data

    total = result[-1]["Count"]
    distribution = [
        {"type": "dialogue", "count": result[0]["Count"]},
        {"type": "chunk", "count": result[1]["Count"]},
        {"type": "statement", "count": result[2]["Count"]},
        {"type": "entity", "count": result[3]["Count"]},
    ]

    data = {"total": total, "distribution": distribution}
    return data


async def search_detials(end_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_DETIALS,
        end_user_id=end_user_id,
    )
    return result


async def search_edges(end_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    result = await _neo4j_connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_EDGES,
        end_user_id=end_user_id,
    )
    return result


async def search_all_batch(end_user_ids: List[str], connector: Neo4jConnector = None) -> Dict[str, int]:
    """批量查询多个用户的记忆数量（简化版本，只返回total）

    Args:
        end_user_ids: 用户ID列表
        connector: 可选的 Neo4j 连接器。为 None 时使用模块级共享 connector（适用于 FastAPI）；
                   Celery 任务中应传入独立 connector 以避免跨 event loop 问题。

    Returns:
        Dict[str, int]: 以user_id为key的记忆数量字典
        格式: {"user_id": total_count}
    """
    if not end_user_ids:
        return {}

    neo4j = connector or _neo4j_connector
    result = await neo4j.execute_query(
        MemoryConfigRepository.SEARCH_FOR_ALL_BATCH,
        end_user_ids=end_user_ids,
    )

    # 转换结果为字典格式，字典格式在查询中无需遍历结果集，直接返回
    data = {}
    for row in result:
        data[row["user_id"]] = row["total"]

    # 为没有数据的用户填充默认值，转换字典格式还为无数据填充默认值
    for user_id in end_user_ids:
        if user_id not in data:
            data[user_id] = 0

    return data


# 热门记忆标签缓存 key 前缀（与 controller、清缓存接口保持一致）
HOT_MEMORY_TAGS_CACHE_PREFIX = "hot_memory_tags"
# 缓存过期：28 小时（= 24h 预热周期 + 4h 安全余量，避免次日预热前空窗）
HOT_MEMORY_TAGS_CACHE_EXPIRE = 100800


async def compute_hot_memory_tags(
        workspace_id: str,
        limit: int = 10,
) -> List[Dict[str, Any]]:
    """计算指定 workspace 的热门记忆标签（不依赖 current_user）。

    供接口实时查询与定时预热任务共用。

    策略：
        1. 按 workspace_id 取所有 end_user_id
        2. 单次批量 Cypher 聚合标签频率（get_raw_tags_batch）
        3. 调用一次 LLM 筛选（filter_tags_with_llm）
        4. 按频率/顺序过滤后返回前 limit 个
    """
    # 防护：limit 可能来自 API 查询参数，非正值会导致 raw_limit 无效（Cypher LIMIT 无意义），
    # 回退到默认值 10
    if limit <= 0:
        limit = 10
    raw_limit = limit * 4

    from app.db import get_db_read
    from app.repositories.end_user_repository import EndUserRepository

    def _get_end_user_ids_in_thread() -> List[str]:
        """独立线程独立 session，避免跨线程共享连接。"""
        with get_db_read() as thread_db:
            end_users = EndUserRepository(thread_db).get_end_users_by_workspace(workspace_id)
            return [str(eu.id) for eu in end_users]

    end_user_ids = await asyncio.to_thread(_get_end_user_ids_in_thread)
    if not end_user_ids:
        return []

    connector = Neo4jConnector()
    try:
        sorted_tags = await get_raw_tags_batch(connector, end_user_ids, limit=raw_limit)
        if not sorted_tags:
            return []

        tag_names = [tag for tag, _ in sorted_tags]
        first_end_user_id = end_user_ids[0]
        filtered_tag_names = await filter_tags_with_llm(tag_names, first_end_user_id)

        filtered_set = set(filtered_tag_names)
        final_tags = [(tag, freq) for tag, freq in sorted_tags if tag in filtered_set]
        top_tags = final_tags[:limit]
        return [{"name": t, "frequency": f} for t, f in top_tags]
    finally:
        await connector.close()


async def analytics_hot_memory_tags(
        db: Session,
        current_user: User,
        limit: int = 10
) -> List[Dict[str, Any]]:
    """获取热门记忆标签（接口入口）。

    从 current_user 取 workspace 后委托 compute_hot_memory_tags。
    签名保持不变（db / current_user 仍保留），controller 调用方零改动。
    """
    workspace_id = current_user.current_workspace_id
    return await compute_hot_memory_tags(str(workspace_id), limit)


async def analytics_recent_activity_stats(workspace_id: Optional[str] = None) -> Dict[str, Any]:
    """获取最近记忆提取活动统计。

    优先从 Redis 缓存读取（按 workspace_id），缓存不存在时降级到日志文件解析。

    Args:
        workspace_id: 工作空间ID，用于从 Redis 读取对应缓存

    Returns:
        包含 total、stats、latest_relative、source 的统计字典
    """
    stats = None
    source = "log"

    # 优先从 Redis 读取
    if workspace_id:
        try:
            from app.cache.memory.activity_stats_cache import ActivityStatsCache
            cached = await ActivityStatsCache.get_activity_stats(workspace_id)
            if cached:
                stats = cached.get("stats", {})
                source = "redis"
                logger.info(f"[ANALYTICS] 从 Redis 读取活动统计: workspace_id={workspace_id}")
        except Exception as e:
            logger.warning(f"[ANALYTICS] 读取 Redis 活动统计失败，降级到日志: {e}")

    # 降级：从日志文件解析
    if stats is None:
        stats, _msg = get_recent_activity_stats()
        source = "log"

    total = (
            stats.get("chunk_count", 0)
            + stats.get("statements_count", 0)
            + stats.get("triplet_entities_count", 0)
            + stats.get("triplet_relations_count", 0)
            + stats.get("temporal_count", 0)
    )

    # 计算"最新一次活动多久前"（仅日志来源时有效）
    latest_relative = None
    if source == "log":
        try:
            info = stats.get("log_path", "")
            idx = info.rfind("最新：")
            if idx != -1:
                latest_path = info[idx + 3:].strip()
                if latest_path and os.path.exists(latest_path):
                    import time
                    diff = max(0.0, time.time() - os.path.getmtime(latest_path))
                    m = int(diff // 60)
                    if m < 1:
                        latest_relative = "刚刚"
                    elif m < 60:
                        latest_relative = "一会前"
                    else:
                        latest_relative = "较早前"
        except Exception:
            pass

    data = {"total": total, "stats": stats, "latest_relative": latest_relative, "source": source}
    return data
