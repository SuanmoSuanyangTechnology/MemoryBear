import logging
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.document_extractor.config import DocExtractorNodeConfig
from app.core.workflow.variable.base_variable import VariableType, FileObject
from app.db import get_db_read
from app.schemas.app_schema import FileInput, FileType, TransferMethod

logger = logging.getLogger(__name__)


def _file_object_to_file_input(f: FileObject) -> FileInput:
    """Convert workflow FileObject to multimodal FileInput."""
    return FileInput(
        type=FileType.DOCUMENT,
        transfer_method=TransferMethod(f.transfer_method),
        url=f.url or None,
        upload_file_id=f.file_id or None,
        file_type=f.origin_file_type or "",
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


class DocExtractorNode(BaseNode):
    """Document Extractor Node.

    Reads one or more file variables and extracts their text content
    by delegating to MultimodalService._extract_document_text.

    Outputs:
        text   (string)        – full concatenated text of all input files
        chunks (array[string]) – per-file extracted text
    """

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "text": VariableType.STRING,
            "chunks": VariableType.ARRAY_STRING,
        }

    def _extract_output(self, business_result: Any) -> Any:
        return business_result

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        return {"file_selector": self.config.get("file_selector")}

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        config = DocExtractorNodeConfig(**self.config)

        raw_val = self.get_variable(config.file_selector, variable_pool, strict=False)
        if raw_val is None:
            logger.warning(f"Node {self.node_id}: file variable '{config.file_selector}' is empty")
            return {"text": "", "chunks": []}

        files = _normalise_files(raw_val)
        if not files:
            return {"text": "", "chunks": []}

        chunks: list[str] = []
        with get_db_read() as db:
            from app.services.multimodal_service import MultimodalService
            svc = MultimodalService(db)
            for f in files:
                try:
                    file_input = _file_object_to_file_input(f)
                    # Ensure URL is populated for local files
                    if not file_input.url:
                        file_input.url = await svc.get_file_url(file_input)
                    # Reuse cached bytes if already fetched
                    if f.get_content():
                        file_input.set_content(f.get_content())
                    text = await svc._extract_document_text(file_input)
                    chunks.append(text)
                except Exception as e:
                    logger.error(
                        f"Node {self.node_id}: failed to extract file url={f.url} file_id={f.file_id}: {e}",
                        exc_info=True,
                    )
                    chunks.append("")

        full_text = "\n\n".join(c for c in chunks if c)
        logger.info(f"Node {self.node_id}: extracted {len(files)} file(s), total chars={len(full_text)}")
        return {"text": full_text, "chunks": chunks}
