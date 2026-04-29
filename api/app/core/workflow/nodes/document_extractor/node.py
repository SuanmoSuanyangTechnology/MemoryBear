import logging
import uuid
from typing import Any

from app.core.config import settings
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.document_extractor.config import DocExtractorNodeConfig
from app.core.workflow.variable.base_variable import VariableType, FileObject
from app.db import get_db_read
from app.models.file_metadata_model import FileMetadata
from app.schemas.app_schema import FileInput, FileType, TransferMethod

logger = logging.getLogger(__name__)


def _file_object_to_file_input(f: FileObject) -> FileInput:
    """Convert workflow FileObject to multimodal FileInput."""
    file_type = f.origin_file_type or ""
    if not file_type and f.mime_type:
        file_type = f.mime_type
    resolved_type = FileType.trans(f.type) if isinstance(f.type, str) else f.type
    if resolved_type != FileType.DOCUMENT:
        raise ValueError(
            f"Document extractor only supports document files, got type '{f.type}' "
            f"(name={f.name or f.file_id or f.url})"
        )
    return FileInput(
        type=resolved_type,
        transfer_method=TransferMethod(f.transfer_method),
        url=f.url or None,
        upload_file_id=f.file_id or None,
        file_type=file_type,
    )


def _normalise_files(val: Any) -> list[FileObject]:
    if isinstance(val, FileObject):
        return [val]
    if isinstance(val, dict) and val.get("is_file"):
        return [FileObject(**val)]
    if isinstance(val, list):
        result: list[FileObject] = []
        for item in val:
            if isinstance(item, FileObject):
                result.append(item)
            elif isinstance(item, dict) and item.get("is_file"):
                result.append(FileObject(**item))
            else:
                logger.warning("Ignoring non-file entry in file list for document extractor: %r", item)
        return result
    return []


async def _save_image_to_storage(
    img_bytes: bytes,
    ext: str,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> tuple[uuid.UUID, str]:
    """
    将图片字节保存到存储后端，写入 FileMetadata，返回 (file_id, url)。
    """
    from app.services.file_storage_service import FileStorageService, generate_file_key

    file_id = uuid.uuid4()
    file_ext = f".{ext}" if not ext.startswith(".") else ext
    content_type = f"image/{ext}"

    file_key = generate_file_key(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file_id=file_id,
        file_ext=file_ext,
    )

    storage_svc = FileStorageService()
    await storage_svc.storage.upload(file_key, img_bytes, content_type)

    with get_db_read() as db:
        meta = FileMetadata(
            id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=file_key,
            file_name=f"doc_image_{file_id}{file_ext}",
            file_ext=file_ext,
            file_size=len(img_bytes),
            content_type=content_type,
            status="completed",
        )
        db.add(meta)
        db.commit()

    url = f"{settings.FILE_LOCAL_SERVER_URL}/storage/permanent/{file_id}"
    return file_id, url


class DocExtractorNode(BaseNode):
    """Document Extractor Node.

    Reads one or more file variables and extracts their text content
    and embedded images.

    Outputs:
        text   (string)        – full text with image placeholders like [图片 第N页 第M张]
        chunks (array[string]) – per-file extracted text (with placeholders)
        images (array[file])   – extracted images as FileObject list, each with
                                 name encoding position: "p{page}_i{index}"
    """

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "text": VariableType.STRING,
            "chunks": VariableType.ARRAY_STRING,
            "images": VariableType.ARRAY_FILE,
        }

    def _extract_output(self, business_result: Any) -> Any:
        return business_result

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        file_selector = self.config.get("file_selector", "")
        # 将变量选择器（如 sys.files）解析为实际值
        resolved = self.get_variable(file_selector, variable_pool, strict=False, default=file_selector)
        return {"file_selector": resolved}

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        config = DocExtractorNodeConfig(**self.config)

        raw_val = self.get_variable(config.file_selector, variable_pool, strict=False)
        if raw_val is None:
            logger.warning(f"Node {self.node_id}: file variable '{config.file_selector}' is empty")
            return {"text": "", "chunks": [], "images": []}

        files = _normalise_files(raw_val)
        if not files:
            return {"text": "", "chunks": [], "images": []}

        tenant_id = uuid.UUID(self.get_variable("sys.tenant_id", variable_pool, strict=False) or str(uuid.uuid4()))
        workspace_id = uuid.UUID(self.get_variable("sys.workspace_id", variable_pool))

        chunks: list[str] = []
        image_file_objects: list[dict] = []

        with get_db_read() as db:
            from app.services.multimodal_service import MultimodalService
            svc = MultimodalService(db)
            for f in files:
                label = f.name or f.url or f.file_id
                try:
                    file_input = _file_object_to_file_input(f)
                    if not file_input.url:
                        file_input.url = await svc.get_file_url(file_input)
                    if f.get_content():
                        file_input.set_content(f.get_content())

                    text = await svc.extract_document_text(file_input)

                    # 从工作流 features 读取 document_image_recognition 开关
                    fu_config = self.workflow_config.get("features", {}).get("file_upload", {})
                    image_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                    if image_recognition:
                        img_infos = await svc.extract_document_images(file_input)
                        for img_info in img_infos:
                            page = img_info["page"]
                            index = img_info["index"]
                            ext = img_info.get("ext", "png")
                            placeholder = f"[图片 第{page}页 第{index + 1}张]" if page > 0 else f"[图片 第{index + 1}张]"
                            try:
                                file_id, url = await _save_image_to_storage(
                                    img_bytes=img_info["bytes"],
                                    ext=ext,
                                    tenant_id=tenant_id,
                                    workspace_id=workspace_id,
                                )
                                image_file_objects.append(FileObject(
                                    type=FileType.IMAGE,
                                    url=url,
                                    transfer_method=TransferMethod.REMOTE_URL,
                                    origin_file_type=f"image/{ext}",
                                    file_id=str(file_id),
                                    name=f"p{page}_i{index}",
                                    mime_type=f"image/{ext}",
                                    is_file=True,
                                ).model_dump())
                                text = text + f"\n{placeholder}: {url}"
                            except Exception as e:
                                logger.error(f"Node {self.node_id}: failed to save image {placeholder}: {e}")

                    chunks.append(text)
                except Exception as e:
                    logger.error(
                        f"Node {self.node_id}: failed to extract file '{label}': {e}",
                        exc_info=True,
                    )
                    chunks.append("")

        full_text = "\n\n".join(c for c in chunks if c)
        logger.info(
            f"Node {self.node_id}: extracted {len(files)} file(s), "
            f"total chars={len(full_text)}, images={len(image_file_objects)}"
        )
        return {"text": full_text, "chunks": chunks, "images": image_file_objects}
