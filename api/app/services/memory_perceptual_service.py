import os
import uuid
from typing import Dict, Any, Optional
from urllib.parse import urlparse, unquote

import json_repair
import langid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import to_timestamp_ms
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.models import RedBearLLM, RedBearModelConfig
from app.db import get_db_context, get_db_read
from app.models import FileMetadata, ModelApiKey, ModelType
from app.models.memory_perceptual_model import PerceptualType, FileStorageService
from app.models.prompt_optimizer_model import RoleType
from app.repositories.end_user_repository import get_end_user_by_id
from app.repositories.memory_perceptual_repository import MemoryPerceptualRepository
from app.repositories.workspace_repository import get_workspace_by_id
from app.schemas import FileType, FileInput
from app.schemas.memory_config_schema import MemoryConfig
from app.schemas.memory_perceptual_schema import (
    PerceptualQuerySchema,
    PerceptualTimelineResponse,
    PerceptualMemoryItem,
    AudioModal, Content, VideoModal, TextModal
)
from app.schemas.model_schema import ModelInfo
from app.services.model_service import ModelApiKeyService
from app.services.multimodal_service import MultimodalService
from app.services.prompt import prompt_manager

business_logger = get_business_logger()


class _PerceptualSnapshot:
    """感知记忆内存快照（不持久化）。

    字段与 MemoryPerceptualModel 对齐，用于试运行等无需写库的场景。
    调用方按属性访问即可（.summary / .meta_data / .file_name 等）。
    与写入主流程（WritePipeline._preprocess_files）共用。
    """
    __slots__ = (
        "id", "end_user_id", "perceptual_type",
        "file_path", "file_name", "file_ext",
        "summary", "meta_data", "created_time",
    )

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))


class MemoryPerceptualService:
    def __init__(self, db: Optional[Session] = None):
        """初始化感知记忆服务。

        Args:
            db: 查询类方法（get_memory_count / get_time_line / get_latest_* 等）必须传入。
                若只调用 ``generate_perceptual_memory``，可以不传：该方法内部自行管理
                短生命周期 Session，避免把 PG 连接持有到多模态推理结束。
        """
        self.db = db
        self.repository = MemoryPerceptualRepository(db) if db is not None else None

    def get_memory_count(self, end_user_id: uuid.UUID) -> Dict[str, Any]:
        """Retrieve perceptual memory statistics for a user."""
        business_logger.info(f"Fetching perceptual memory statistics: end_user_id={end_user_id}")
        try:
            total_count = self.repository.get_count_by_user_id(end_user_id=end_user_id)

            vision_count = self.repository.get_count_by_type(end_user_id, PerceptualType.VISION)
            audio_count = self.repository.get_count_by_type(end_user_id, PerceptualType.AUDIO)
            text_count = self.repository.get_count_by_type(end_user_id, PerceptualType.TEXT)
            conversation_count = self.repository.get_count_by_type(end_user_id, PerceptualType.CONVERSATION)

            stats = {
                "total": total_count,
                "by_type": {
                    "vision": vision_count,
                    "audio": audio_count,
                    "text": text_count,
                    "conversation": conversation_count
                }
            }

            business_logger.info(f"Memory statistics fetched successfully: total={total_count}")
            return stats

        except Exception as e:
            business_logger.error(f"Failed to fetch memory statistics: {str(e)}")
            raise BusinessException(f"Failed to fetch memory statistics: {str(e)}", BizCode.DB_ERROR)

    def _get_latest_memory_by_type(
            self,
            end_user_id: uuid.UUID,
            perceptual_type: PerceptualType
    ) -> Optional[dict[str, Any]]:
        """Internal helper to retrieve the latest memory by type."""
        business_logger.info(f"Fetching latest {perceptual_type.name.lower()} memory: end_user_id={end_user_id}")
        try:
            memories = self.repository.get_by_type(
                end_user_id=end_user_id,
                perceptual_type=perceptual_type,
                limit=1,
                offset=0
            )
            if not memories:
                business_logger.info(f"No {perceptual_type.name.lower()} memory found: end_user_id={end_user_id}")
                return None

            memory = memories[0]
            meta_data = memory.meta_data or {}
            modalities = meta_data.get("modalities")
            content = meta_data.get("content")

            if not modalities:
                raise BusinessException(f"Modalities not defined, perceptual memory_id={memory.id}", BizCode.DB_ERROR)
            if not content:
                raise BusinessException(f"Content not defined, perceptual memory_id={memory.id}", BizCode.DB_ERROR)
            content = Content(**content)
            match perceptual_type:
                case PerceptualType.VISION:
                    modal = VideoModal(**modalities)
                case PerceptualType.AUDIO:
                    modal = AudioModal(**modalities)
                case PerceptualType.TEXT:
                    modal = TextModal(**modalities)
                case _:
                    raise BusinessException("Unsupported perceptual type", BizCode.DB_ERROR)
            detail = modal.model_dump()

            result = {
                "id": str(memory.id),
                "file_name": memory.file_name,
                "file_path": memory.file_path,
                "storage_type": memory.storage_service,
                "summary": memory.summary,
                "keywords": content.keywords,
                "topic": content.topic,
                "domain": content.domain,
                "created_time": to_timestamp_ms(memory.created_time),
                **detail
            }

            business_logger.info(
                f"Latest {perceptual_type.name.lower()} memory retrieved successfully: file={memory.file_name}")
            return result

        except Exception as e:
            business_logger.error(f"Failed to fetch latest {perceptual_type.name.lower()} memory: {str(e)}",
                                  exc_info=True)
            raise BusinessException(f"Failed to fetch latest {perceptual_type.name.lower()} memory: {str(e)}",
                                    BizCode.DB_ERROR)

    def get_latest_visual_memory(self, end_user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._get_latest_memory_by_type(end_user_id, PerceptualType.VISION)

    def get_latest_audio_memory(self, end_user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._get_latest_memory_by_type(end_user_id, PerceptualType.AUDIO)

    def get_latest_text_memory(self, end_user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._get_latest_memory_by_type(end_user_id, PerceptualType.TEXT)

    def get_time_line(self, end_user_id: uuid.UUID, query: PerceptualQuerySchema) -> PerceptualTimelineResponse:
        """Retrieve a timeline of perceptual memories for a user."""
        business_logger.info(f"Fetching perceptual memory timeline: "
                             f"end_user_id={end_user_id}, filter={query.filter}")

        try:
            if query.page < 1:
                raise BusinessException("Page number must be greater than 0", BizCode.INVALID_PARAMETER)
            if query.page_size < 1 or query.page_size > 100:
                raise BusinessException("Page size must be between 1 and 100", BizCode.INVALID_PARAMETER)

            total_count, memories = self.repository.get_timeline(end_user_id, query)

            memory_items = []
            for memory in memories:
                meta_data = memory.meta_data or {}
                content = meta_data.get("content", {})

                # 安全地提取 content 字段，提供默认值
                if content:
                    content_obj = Content(**content)
                    topic = content_obj.topic
                    domain = content_obj.domain
                    keywords = content_obj.keywords
                else:
                    topic = "Unknown"
                    domain = "Unknown"
                    keywords = []

                memory_item = PerceptualMemoryItem(
                    id=memory.id,
                    perceptual_type=PerceptualType(memory.perceptual_type),
                    file_path=memory.file_path,
                    file_name=memory.file_name,
                    file_ext=memory.file_ext,
                    summary=memory.summary,
                    meta_data=meta_data,
                    topic=topic,
                    domain=domain,
                    keywords=keywords,
                    created_time=to_timestamp_ms(memory.created_time),
                    storage_service=FileStorageService(memory.storage_service),
                )
                memory_items.append(memory_item)

            timeline_response = PerceptualTimelineResponse(
                total=total_count,
                page=query.page,
                page_size=query.page_size,
                total_pages=(total_count + query.page_size - 1) // query.page_size,
                memories=memory_items
            )

            business_logger.info(f"Perceptual memory timeline retrieved successfully: "
                                 f"total={total_count}, returned={len(memories)}")
            return timeline_response

        except BusinessException:
            raise
        except Exception as e:
            business_logger.error(f"Failed to fetch perceptual memory timeline: {str(e)}")
            raise BusinessException(f"Failed to fetch perceptual memory timeline: {str(e)}", BizCode.DB_ERROR)

    def _get_mutlimodal_client(
            self,
            db: Session,
            file_type: FileType,
            config: MemoryConfig,
            tenant_id: uuid.UUID,
    ) -> tuple[RedBearLLM | None, ModelApiKey | None]:
        """取多模态模型客户端。db 由调用方在事务内传入。"""
        model_config = None
        if file_type == FileType.AUDIO:
            model_config = ModelApiKeyService.get_available_api_key(
                db,
                config.audio_model_id,
                tenant_id=tenant_id
            )
        elif file_type == FileType.VIDEO:
            model_config = ModelApiKeyService.get_available_api_key(
                db,
                config.video_model_id,
                tenant_id=tenant_id
            )
        elif file_type == FileType.DOCUMENT:
            model_config = ModelApiKeyService.get_available_api_key(
                db,
                config.llm_model_id,
                tenant_id=tenant_id
            )
        elif file_type == FileType.IMAGE:
            model_config = ModelApiKeyService.get_available_api_key(
                db,
                config.vision_model_id,
                tenant_id=tenant_id
            )
        llm = None
        if model_config:
            llm = RedBearLLM(
                RedBearModelConfig(
                    model_name=model_config.model_name,
                    provider=model_config.provider,
                    api_key=model_config.api_key,
                    base_url=model_config.api_base,
                    is_omni=model_config.is_omni,
                    capability=model_config.capability,
                )
            )
        return llm, model_config

    async def generate_perceptual_memory(
            self,
            end_user_id: str,
            memory_config: MemoryConfig,
            file: FileInput,
            content: str | None = None,
            persist: bool = True,
    ):
        """生成感知记忆。

        连接管理：每处用到 DB 都现开现还一个短 Session，绝不把 PG 连接持有到
        LLM 推理结束（memory_tasks 并发 100 > 连接池 70，长持有会打满池）。
        注意 ORM 对象出了 with 块就会 Detached，块内要先取成普通值。

        参数：
            persist=True  — 默认，写入 memory_perceptual 表。
            persist=False — 试运行场景，不写库。同时绕开
                            end_user → workspace → tenant_id 的 DB 查询，
                            直接从 memory_config.tenant_id 取。

        返回：
            _PerceptualSnapshot | None（两种模式统一返回内存快照）
        """
        # 用 DB：解析 tenant_id + 取模型配置。ModelApiKey 出块即废，块内固化成 ModelInfo。
        with get_db_read() as db:
            if persist:
                end_user = get_end_user_by_id(db, end_user_id)
                workspace_id = end_user.workspace_id
                workspace = get_workspace_by_id(db, workspace_id)
                tenant_id = workspace.tenant_id
            else:
                tenant_id = memory_config.tenant_id
            llm, model_config = self._get_mutlimodal_client(db, file.type, memory_config, tenant_id)
            if model_config is None or llm is None:
                return None
            api_config = ModelInfo(
                model_name=model_config.model_name,
                provider=model_config.provider,
                api_key=model_config.api_key,
                api_base=model_config.api_base,
                is_omni=model_config.is_omni,
                capability=model_config.capability,
                model_type=ModelType.LLM
            )

        # 用 DB：文件预处理（本地文件需查 FileMetadata 取文件名）
        with get_db_read() as db:
            file_message = await MultimodalService(db, api_config).process_files(
                files=[file]
            )
        if not file_message:
            business_logger.warning(f"Unsupported file type {file}, model capability: {api_config.capability}")
            return None
        file_message = file_message[0]
        try:
            rendered_system_message = prompt_manager.render(
                'perceptual_summary_system',
                file_type=file.type,
                language=langid.classify(content)[0] if content else "zh"
            )
        except FileNotFoundError as e:
            business_logger.error(f"Failed to generate perceptual memory: {str(e)}")
            return None
        messages = [
            {"role": RoleType.SYSTEM.value, "content": [{"type": "text", "text": rendered_system_message}]},
            {"role": RoleType.USER.value, "content": [
                file_message,
                {"type": "text", "text": "Generate a summary of the file's content"}
            ]}
        ]
        # 不持有连接：多模态推理（最慢的一段）
        try:
            result = await llm.ainvoke(messages)
        except Exception as e:
            business_logger.error(f"Failed to generate perceptual memory: {str(e)}")
            return None
        content = result.content
        final_output = ""
        if isinstance(content, list):
            for msg in content:
                if isinstance(msg, dict):
                    final_output += msg.get("text", "")
                elif isinstance(msg, str):
                    final_output += msg
        elif isinstance(content, dict):
            final_output += content.get("text", "")
        elif isinstance(content, str):
            final_output = content
        else:
            raise ValueError(f"Unexcept Model Output Type: {result.content}")
        content = json_repair.repair_json(final_output, return_objects=True)
        path = urlparse(file.url).path
        filename = os.path.basename(path)
        filename = unquote(filename)
        file_ext = os.path.splitext(filename)[1]
        try:
            file_id = uuid.UUID(filename)
            stmt = select(FileMetadata).where(
                FileMetadata.id == file_id
            )
            # 用 DB：回查本地文件真实名称（远程文件 filename 不是 UUID，走不到这）
            with get_db_read() as db:
                file_obj = db.execute(stmt).scalar_one_or_none()

                if file_obj:
                    filename = file_obj.file_name
                    file_ext = file_obj.file_ext
        except ValueError:
            business_logger.debug(f"Remote file, file_id={filename}")
        if not file_ext:
            if file.type == FileType.AUDIO:
                file_ext = ".mp3"
            elif file.type == FileType.VIDEO:
                file_ext = ".mp4"
            elif file.type == FileType.DOCUMENT:
                file_ext = ".txt"
            elif file.type == FileType.IMAGE:
                file_ext = ".jpg"
            filename += file_ext
        file_content = {
            "keywords": content.get("keywords", []),
            "topic": content.get("topic"),
            "domain": content.get("domain")
        }
        if file.type in [FileType.IMAGE, FileType.VIDEO]:
            file_modalities = {
                "scene": content.get("scene", [])
            }
        elif file.type in [FileType.DOCUMENT]:
            file_modalities = {
                "section_count": content.get("section_count", 0),
                "title": content.get("title", ""),
                "first_line": content.get("first_line", "")
            }
        else:
            file_modalities = {
                "speaker_count": content.get("speaker_count", 0)
            }
        if not persist:
            # 试运行：返回内存快照，不写库
            return _PerceptualSnapshot(
                id=None,
                end_user_id=end_user_id,
                perceptual_type=PerceptualType.trans_from_file_type(file.type),
                file_path=file.url,
                file_name=filename,
                file_ext=file_ext,
                summary=content.get("summary", ""),
                meta_data={
                    "content": file_content,
                    "modalities": file_modalities,
                },
                created_time=None,
            )
        # 用 DB：落库。返回快照而非 ORM 对象，否则调用方出块访问就 Detached。
        with get_db_context() as db:
            memory = MemoryPerceptualRepository(db).create_perceptual_memory(
                end_user_id=uuid.UUID(end_user_id),
                perceptual_type=PerceptualType.trans_from_file_type(file.type),
                file_path=file.url,
                file_name=filename,
                file_ext=file_ext,
                summary=content.get('summary', ""),
                meta_data={
                    "content": file_content,
                    "modalities": file_modalities
                }
            )
            # 在 commit 前取值：commit 会 expire 属性，之后访问需额外一次 SELECT
            snapshot = _PerceptualSnapshot(
                id=memory.id,
                end_user_id=end_user_id,
                perceptual_type=memory.perceptual_type,
                file_path=memory.file_path,
                file_name=memory.file_name,
                file_ext=memory.file_ext,
                summary=memory.summary,
                meta_data=memory.meta_data,
                created_time=memory.created_time,
            )
            db.commit()
        return snapshot
