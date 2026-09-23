"""公共文件内容读取能力（业务侧共享）。

私有化部署下，用户上传的文件只在内网对象存储可达，外部/公网模型无法通过 URL 回拉
内容；而本地文件（transfer_method=local_file）在消息元数据、变量池与工作流 FileObject
里只保留 file_id / upload_file_id，url 是空串。因此任何需要把附件内容交给下游的入口
（多模态 LLM 出站、知识库图片检索、文档抽取、工具调用）都必须能凭 file_id 自己去存储
后端取字节，不能假设 URL 可达。

本模块集中三件事，避免每个消费点各写一遍：

1. resolve —— 从 file_id / 本服务永久 URL 定位文件，并按 workspace/tenant 校验归属；
2. read    —— 从存储后端直读字节（不经过 HTTP，不依赖 URL 可达性）；
3. encode  —— 按下游契约编码（图片归一化 + base64 data URI）。

口径约定：URL 在本平台只是「展示 / 内部取字节」的句柄，不是出站契约；凡是要离开本服务
边界的内容（发给模型、发给知识库）一律走 bytes / data URI。
"""

from __future__ import annotations

import base64
import io
import logging
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.db import get_async_db_context
from app.models.file_metadata_model import FileMetadata
from app.services.file_storage_service import FileStorageService

logger = logging.getLogger(__name__)

# 本服务自铸的永久下载 URL 路径标记（见 file_storage_controller 的 permanent 路由）。
_PERMANENT_PATH_MARKER = "/storage/permanent/"

# 下游（记忆熊知识库）接受的图片字节上限，与那边校验保持一致：超限直接判失败，
# 没必要把大文件读完再被拒。
IMAGE_MAX_BYTES = 10 * 1024 * 1024

# 下游只接受这几种媒体类型，且会与真实图片格式交叉校验，所以媒体类型必须由
# 真实格式推导，不能用声明值。
_PIL_FORMAT_TO_MEDIA = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


@dataclass(frozen=True, slots=True)
class FileReference:
    """附件引用的归一化视图，兼容 dict / FileObject / FileInput 三种载荷。

    字段来源对三类载荷逐一映射，消费点不再各自 getattr/get：
    - 类型：``type``（可能带 MIME 后缀，如 image/png）→ 回退 ``origin_file_type``；
    - 文件标识：``file_id``（FileObject）→ 回退 ``upload_file_id``（FileInput / 前端格式）；
    - 地址：``url``（remote_url 才有，本地文件是空串）；
    - 传递方式：``transfer_method``，用来区分「本地文件」与「远程地址」两条取字节路径。
    """

    file_type: str | None = None
    url: str | None = None
    file_id: str | None = None
    name: str | None = None
    transfer_method: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> "FileReference | None":
        """把任意附件载荷归一化成引用；无法识别出任何标识时返回 None。"""
        if payload is None or isinstance(payload, (str, bytes, int, float, bool)):
            return None

        if isinstance(payload, dict):
            def _field(key: str) -> Any:
                return payload.get(key)
        else:
            def _field(key: str) -> Any:
                return getattr(payload, key, None)

        raw_type = _field("type") or _field("origin_file_type")
        file_type = None
        if raw_type is not None:
            # type 可能是 FileType 枚举，取其字面值
            file_type = str(getattr(raw_type, "value", raw_type)).strip() or None

        raw_url = _field("url")
        url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None

        raw_file_id = _field("file_id") or _field("upload_file_id")
        file_id = str(raw_file_id).strip() if raw_file_id else None

        raw_name = _field("name")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else None

        raw_method = _field("transfer_method")
        transfer_method = None
        if raw_method is not None:
            transfer_method = str(getattr(raw_method, "value", raw_method)).strip().lower() or None

        if file_type is None and url is None and file_id is None:
            return None
        return cls(
            file_type=file_type,
            url=url,
            file_id=file_id,
            name=name,
            transfer_method=transfer_method,
        )

    @property
    def is_image(self) -> bool:
        return bool(self.file_type) and self.file_type.startswith("image")

    @property
    def is_local(self) -> bool:
        """显式声明的本地上传件（transfer_method=local_file）。"""
        return self.transfer_method == "local_file"

    @property
    def locator(self) -> str | None:
        """日志 / 审计 / 缓存键用的稳定短标识，不含 base64 或大段 URL。"""
        if self.url:
            return self.url
        if self.file_id:
            return f"file_id:{self.file_id}"
        return None


@dataclass(frozen=True, slots=True)
class FileContent:
    """一次附件读取的结果。"""

    data: bytes
    file_id: str | None = None
    file_name: str | None = None
    content_type: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)


def build_permanent_file_url(file_id: uuid.UUID | str) -> str:
    """本服务自铸的永久下载 URL（仅作内部句柄，不出站给模型）。"""
    return f"{settings.FILE_LOCAL_SERVER_URL.rstrip('/')}{_PERMANENT_PATH_MARKER}{file_id}"


def extract_permanent_file_id(url: Any, *, trust_any_host: bool = False) -> str | None:
    """识别本服务自铸的永久下载 URL，并取出其中的文件 ID。

    trust_any_host=False（URL 来自请求，不可信）：只认当前 FILE_LOCAL_SERVER_URL
    前缀，避免任意公网 URL 被误判成本服务自有文件而触发服务端回拉（SSRF）。
    trust_any_host=True（URL 来自已持久化的元数据 / 变量池，可信）：额外接受路径
    形态，这样域名或端口变更后，旧数据里的永久 URL 仍能还原成文件 ID 自行取字节。
    """
    if not isinstance(url, str):
        return None
    normalized_url = url.split("?", 1)[0]
    configured_prefix = f"{settings.FILE_LOCAL_SERVER_URL.rstrip('/')}{_PERMANENT_PATH_MARKER}"
    if normalized_url.startswith(configured_prefix):
        candidate = normalized_url[len(configured_prefix):]
    elif trust_any_host:
        path = urlparse(normalized_url).path
        marker_index = path.rfind(_PERMANENT_PATH_MARKER)
        if marker_index < 0:
            return None
        candidate = path[marker_index + len(_PERMANENT_PATH_MARKER):]
    else:
        return None

    candidate = candidate.strip("/")
    try:
        return str(uuid.UUID(candidate))
    except (TypeError, ValueError):
        return None


def encode_image_data_uri(data: bytes) -> str | None:
    """把图片字节归一化成下游要求的 base64 data URI；非图片或超限返回 None。

    与知识库集成层的图片编码口径保持一致：GIF 压平首帧转 JPEG，只允许
    JPEG/PNG/WEBP/BMP，且不超过 IMAGE_MAX_BYTES。
    """
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            actual_format = (image.format or "").upper()

            if actual_format == "GIF":
                # 下游不支持 GIF：把首帧压平到白底后转 JPEG
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                buffer = io.BytesIO()
                flattened.save(buffer, format="JPEG", quality=90)
                data = buffer.getvalue()
                actual_format = "JPEG"

            media_type = _PIL_FORMAT_TO_MEDIA.get(actual_format)
    except (OSError, SyntaxError, ValueError) as exc:
        logger.info("file_content_image_decode_failed error=%s", type(exc).__name__)
        return None

    if media_type is None or len(data) > IMAGE_MAX_BYTES:
        logger.info(
            "file_content_image_rejected media=%s bytes=%s",
            media_type,
            len(data) if data else 0,
        )
        return None

    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _as_uuid(value: Any) -> uuid.UUID | None:
    """把 workspace_id / tenant_id 规范化成 UUID；无法解析时返回 None。"""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        logger.debug("file_content_scope_id_unparsable value=%r", value)
        return None


@dataclass(frozen=True, slots=True)
class _FileMetadataSnapshot:
    """会话关闭后仍可安全使用的元数据快照（避免接触已分离的 ORM 实例）。"""

    file_id: str
    file_key: str
    file_name: str | None
    file_size: int
    content_type: str | None


async def _load_file_metadata(
    session: AsyncSession | Session,
    file_id: str,
    workspace_id: Any,
    tenant_id: Any,
) -> _FileMetadataSnapshot | None:
    """按归属范围查询文件元数据，防止跨租户读取。

    租户是硬隔离边界，优先按 tenant_id 过滤；工作空间的维度不额外收紧——共享应用
    场景下「上传者所在工作空间」与「应用所属工作空间」可能不同，而文件对同租户仍是
    合法数据，收紧会变成查不到。只有拿不到 tenant 时才退到 workspace 维度。
    两者都拿不到则拒绝读取（返回 None，由调用方给出明确错误）。
    """
    try:
        normalized_file_id = uuid.UUID(str(file_id))
    except (TypeError, ValueError):
        return None

    scoped_tenant_id = _as_uuid(tenant_id)
    scoped_workspace_id = _as_uuid(workspace_id)
    if scoped_tenant_id is not None:
        scope_condition = FileMetadata.tenant_id == scoped_tenant_id
    elif scoped_workspace_id is not None:
        scope_condition = FileMetadata.workspace_id == scoped_workspace_id
    else:
        return None

    conditions = [
        FileMetadata.id == normalized_file_id,
        FileMetadata.status == "completed",
        scope_condition,
    ]
    if isinstance(session, AsyncSession):
        metadata = (await session.execute(select(FileMetadata).where(*conditions))).scalar_one_or_none()
    else:
        metadata = session.query(FileMetadata).filter(*conditions).first()

    if metadata is None:
        return None
    return _FileMetadataSnapshot(
        file_id=str(metadata.id),
        file_key=metadata.file_key,
        file_name=metadata.file_name,
        file_size=int(metadata.file_size or 0),
        content_type=metadata.content_type,
    )


async def _read_by_file_id(
    reference: FileReference,
    *,
    workspace_id: Any,
    tenant_id: Any,
    db: AsyncSession | Session | None,
    limit: int,
) -> FileContent:
    """本地文件：按归属范围查询元数据 + 存储后端直读，全程不出站（不经 HTTP）。"""
    if workspace_id is None and tenant_id is None:
        # 与多模态出站同一口径：没有归属上下文就不允许读本地文件
        raise BusinessException(
            "缺少工作空间或租户上下文，无法读取本地文件",
            BizCode.FILE_NOT_FOUND,
        )

    if db is not None:
        metadata = await _load_file_metadata(db, reference.file_id, workspace_id, tenant_id)
    else:
        async with get_async_db_context() as session:
            metadata = await _load_file_metadata(session, reference.file_id, workspace_id, tenant_id)

    if metadata is None:
        # 对调用方统一返回不可用，避免泄露其它租户的文件存在性
        raise BusinessException(
            "文件不存在、未完成或无权访问",
            BizCode.FILE_NOT_FOUND,
        )

    declared_size = metadata.file_size
    if declared_size > limit:
        raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)

    content = await FileStorageService().download_file(metadata.file_key)
    if len(content) > limit:
        raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)
    if declared_size and len(content) != declared_size:
        logger.error(
            "本地文件大小与元数据不一致",
            extra={
                "file_id": metadata.file_id,
                "expected_size": declared_size,
                "actual_size": len(content),
            },
        )
        raise BusinessException("文件读取失败", BizCode.FILE_READ_ERROR)

    return FileContent(
        data=content,
        file_id=metadata.file_id,
        file_name=metadata.file_name,
        content_type=metadata.content_type,
    )


async def _read_by_permanent_url(reference: FileReference, *, limit: int) -> FileContent:
    """旧数据兜底：仅认本服务自铸的永久 URL，还原 file_id 后由本服务自取字节。"""
    file_id = extract_permanent_file_id(reference.url, trust_any_host=True)
    if file_id is None:
        raise BusinessException(
            "附件 URL 不是本服务可读取的文件地址",
            BizCode.FILE_READ_ERROR,
        )

    # 用还原出的 file_id 重新拼本机地址请求，避免信任外来 URL 的 host（SSRF）
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(build_permanent_file_url(file_id), follow_redirects=True)
            response.raise_for_status()
            content = response.content
    except Exception as exc:
        logger.error(f"读取本服务附件 URL 失败: {exc}", exc_info=True)
        raise BusinessException("文件读取失败", BizCode.FILE_READ_ERROR)

    if len(content) > limit:
        raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)

    return FileContent(
        data=content,
        file_id=file_id,
        content_type=response.headers.get("content-type"),
    )


async def read_file_content(
    payload: FileReference | Any,
    *,
    workspace_id: Any = None,
    tenant_id: Any = None,
    db: AsyncSession | Session | None = None,
    max_bytes: int | None = None,
) -> FileContent:
    """读取附件内容，优先走 file_id 直读存储，其次走本服务永久 URL。

    Args:
        payload: FileReference / dict（FileObject）/ FileInput 均可。
        workspace_id: 归属工作空间；拿不到 tenant_id 时用它做范围校验兜底。
        tenant_id: 归属租户，优先使用（硬隔离边界）。
            两者至少给一个，否则拒绝读取本地文件。
        db: 已有会话；不传则内部开一个短会话（用完即关，不参与调用方事务）。
        max_bytes: 单文件上限，默认 settings.MAX_FILE_SIZE。

    Raises:
        BusinessException: 引用不可用、无权访问、超限或存储读取失败。
    """
    reference = payload if isinstance(payload, FileReference) else FileReference.from_payload(payload)
    if reference is None:
        raise BusinessException("附件引用无效，无法读取文件内容", BizCode.FILE_READ_ERROR)

    limit = int(max_bytes) if max_bytes else int(settings.MAX_FILE_SIZE)

    # 本地文件（url 为空，或显式标了 local_file）优先按 file_id 直读存储；
    # 远程地址只有在能还原成本服务自铸的 file_id 时才走本地读取，否则老实交给
    # 调用方按 URL 处理——不能拿一个远程文件的随机 file_id 去查库，那是徒劳。
    if reference.file_id and (reference.is_local or not reference.url):
        return await _read_by_file_id(
            reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            limit=limit,
        )
    if reference.url:
        return await _read_by_permanent_url(reference, limit=limit)
    if reference.file_id:
        return await _read_by_file_id(
            reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            limit=limit,
        )

    raise BusinessException(
        "附件缺少文件标识（file_id / URL），无法读取内容",
        BizCode.FILE_READ_ERROR,
    )


async def try_read_file_content(
    payload: FileReference | Any,
    *,
    workspace_id: Any = None,
    tenant_id: Any = None,
    db: AsyncSession | Session | None = None,
    max_bytes: int | None = None,
) -> FileContent | None:
    """读取失败只记日志并返回 None，供「单个附件失败不影响其它附件」的场景使用。"""
    reference = payload if isinstance(payload, FileReference) else FileReference.from_payload(payload)
    try:
        return await read_file_content(
            reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            max_bytes=max_bytes,
        )
    except BusinessException as exc:
        logger.warning(
            "附件内容读取失败，已跳过: %s locator=%s",
            exc,
            reference.locator if reference else None,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - 兜底，不能因单个附件打断整轮
        logger.warning(
            "附件内容读取异常，已跳过: %s locator=%s",
            exc,
            reference.locator if reference else None,
            exc_info=True,
        )
        return None


async def resolve_image_retrieval_query(
    payload: FileReference | Any,
    *,
    workspace_id: Any = None,
    tenant_id: Any = None,
    db: AsyncSession | Session | None = None,
) -> Any | None:
    """把图片引用转成知识库侧要求的图片检索 query（data URI 形态），失败返回 None。

    - 本地引用（transfer_method=local_file，或 url 为空只有 file_id）：直读存储字节后
      本地编码，私有化环境下不依赖 URL 可达；
    - 远程 URL：若能还原成本服务自铸的 file_id 同样直读，否则复用知识库集成层的
      下载 + 校验实现，保持两边口径一致。

    返回值为 ``app.schemas.chunk_schema.ImageRetrievalQuery``（在此处延迟导入，
    避免 services 与 schemas 的导入顺序耦合）。
    """
    from app.integrations.knowledge.retrieval_policy import build_image_retrieval_query
    from app.schemas.chunk_schema import ImageRetrievalQuery

    reference = payload if isinstance(payload, FileReference) else FileReference.from_payload(payload)
    if reference is None:
        return None

    # 决定是否走「本地自取字节」：本地文件直接可以；远程地址只有在能还原成本服务
    # 自铸的永久 URL 时才转成 file_id 直读（私有化下这条路径不依赖 URL 可达）。
    direct_reference: FileReference | None = None
    if reference.is_local or not reference.url:
        direct_reference = reference
    else:
        permanent_file_id = extract_permanent_file_id(reference.url, trust_any_host=True)
        if permanent_file_id:
            direct_reference = FileReference(
                file_type=reference.file_type,
                file_id=permanent_file_id,
                name=reference.name,
                transfer_method="local_file",
            )

    if direct_reference is not None:
        content = await try_read_file_content(
            direct_reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            max_bytes=IMAGE_MAX_BYTES,
        )
        if content is not None:
            data_uri = encode_image_data_uri(content.data)
            if data_uri is None:
                logger.warning(
                    "图片内容无法编码为检索用 data URI locator=%s bytes=%s",
                    direct_reference.locator,
                    content.size,
                )
            else:
                logger.info(
                    "图片检索 query 已由本地字节编码 locator=%s bytes=%s",
                    direct_reference.locator,
                    content.size,
                )
                return ImageRetrievalQuery(modality="image", content=data_uri)
        # 本地读取失败时若还带着 URL，继续走下面的 URL 通道兜底

    if reference.url:
        return await build_image_retrieval_query(reference.url)

    return None
