"""Model gateway adapter: resolve read-context model slots into callable clients.

Credentials never live in this process. The gateway only names a
``model_configs`` row plus the tenant it acts for and
:class:`~src.infrastructure.model_service.ModelServiceManager` resolves the
credential, channel and failover inside model-service (design §6.3). That is why
:meth:`ProfileModelGateway.perceptual_model_view` returns a *non-decrypted*
capability view (provider, model type, modality gates) instead of the host's
``ModelInfo`` with its (now always empty) ``api_key``.

Slot semantics copied from the host pipeline:

* ``chat``            → ``memory_config.llm_model_id``, LLM family;
* ``embedding``       → ``memory_config.embedding_model_id``; ``None`` when the
  slot is empty so the ported retriever degrades to full-text search;
* ``rerank``          → ``memory_config.rerank_model_id``; ``None`` when empty so
  the retriever falls back to score fusion;
* ``perceptual_chat`` → vision (1) / audio (2) / llm (3) slots, all through the
  chat family exactly like the host (``get_llm_client_async``);
* ``multimodal_formatter`` → the read-only part of the host's
  ``MultimodalService``: remote-URL attachments are turned into provider-native
  chat content blocks (``image_url`` for OpenAI-compatible channels, base64 for
  Bedrock/Anthropic).

Documented fallbacks (no credential and no byte store here):

* an attachment that must be hydrated from platform file storage
  (``transfer_method=local_file``, a ``/storage/permanent/`` URL, or a private
  URL reached through ``upload_file_id``) cannot be read: file storage needs the
  API process' storage credentials and workspace-scoped file metadata, so the
  formatter yields no block and the perceptual memory keeps its stored summary —
  the same end state the host reaches for an unusable attachment with
  ``include_processing_errors=False``;
* audio/video never occur on the read path and are skipped;
* PDF/DOCX/XLSX text extraction and PDF embedded images need optional parser
  packages that memory-profile does not declare (``pypdf``, ``python-docx``,
  ``openpyxl``, ``pymupdf``, ``python-magic``). They are imported lazily and a
  missing package degrades to "no block" instead of failing the read;
* plain-text decoding uses UTF-8 with replacement instead of the host's
  ``chardet`` detection (also not declared), which only matters for exotic
  legacy encodings.
"""

from __future__ import annotations

import base64
import io
import logging
import zipfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from ipaddress import ip_address
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from langchain_core.documents import Document
from redbear_model import Modality, ModelProfile

from src.core.domain.errors import ModelUnavailableError
from src.core.domain.models import ReadContext
from src.repositories.model_config import ModelConfigRepository

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from src.infrastructure.model_service import ModelServiceManager
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

#: Perceptual slot types of the host protocol: vision, audio, plain text.
PERCEPTUAL_VISION = 1
PERCEPTUAL_AUDIO = 2
PERCEPTUAL_TEXT = 3

_IMAGE = Modality.IMAGE.value
_DOCUMENT = "document"
_AUDIO = Modality.AUDIO.value
_VIDEO = Modality.VIDEO.value
_LOCAL_FILE = "local_file"
_PERMANENT_FILE_MARKER = "/storage/permanent/"

#: Providers whose image/document blocks are base64 payloads, not URLs.
_BASE64_PROVIDERS = frozenset({"bedrock", "anthropic"})

_TEXT_MIME = frozenset({"text/plain", "text/x-markdown"})
_PDF_MIME = frozenset({"application/pdf"})
_DOC_MIME = frozenset(
    {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
_XLSX_MIME = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }
)
_CSV_MIME = frozenset({"text/csv", "application/csv"})
_JSON_MIME = frozenset({"application/json"})

#: Extension → document family, used when the MIME type cannot be detected.
_EXTENSION_FAMILIES = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".pdf": "pdf",
    ".doc": "word",
    ".docx": "word",
    ".xls": "excel",
    ".xlsx": "excel",
    ".csv": "csv",
    ".json": "json",
}

#: Read-only projection of the ``model_configs`` columns the gates need lives in
#: ``ModelConfigRepository.get_slot_view``; this module never issues SQL itself.


# ────────────────────────────────────────────────────────────────
# Model clients
# ────────────────────────────────────────────────────────────────


class _EmbeddingAdapter:
    """``aembed_query`` over the embedding family of model-service."""

    def __init__(
        self,
        service: ModelServiceManager,
        config_id: UUID,
        tenant_id: UUID,
    ) -> None:
        self._service = service
        self._config_id = config_id
        self._tenant_id = tenant_id

    async def aembed_query(self, text: str) -> list[float]:
        vectors = await self._service.embed_texts(
            self._config_id, self._tenant_id, [text]
        )
        return vectors[0] if vectors else []


class _RerankAdapter:
    """``compress_documents`` over the rerank family of model-service.

    Mirrors the host's ``RedBearRerank._compressed``: a new ``Document`` per
    ranked hit, carrying the original metadata plus ``relevance_score``. The
    input position is added as ``index`` because the ported retriever maps the
    reranked documents back onto its candidate list by that key.
    """

    def __init__(
        self,
        service: ModelServiceManager,
        config_id: UUID,
        tenant_id: UUID,
    ) -> None:
        self._service = service
        self._config_id = config_id
        self._tenant_id = tenant_id

    async def compress_documents(
        self,
        documents: Sequence[Any],
        query: str,
        *,
        top_n: int | None = None,
    ) -> list[Document]:
        texts = [_document_text(document) for document in documents]
        hits = await self._service.rerank(
            self._config_id,
            self._tenant_id,
            query=query,
            documents=texts,
            top_n=top_n,
        )
        compressed: list[Document] = []
        for hit in hits:
            if hit.index < 0 or hit.index >= len(texts):
                logger.warning(
                    "rerank_result_index_out_of_range index=%s documents=%s",
                    hit.index,
                    len(texts),
                )
                continue
            metadata = getattr(documents[hit.index], "metadata", None) or {}
            compressed.append(
                Document(
                    page_content=texts[hit.index],
                    metadata={
                        **deepcopy(dict(metadata)),
                        "index": hit.index,
                        "relevance_score": hit.relevance_score,
                    },
                )
            )
        return compressed


def _document_text(document: Any) -> str:
    page_content = getattr(document, "page_content", None)
    return page_content if isinstance(page_content, str) else str(document)


# ────────────────────────────────────────────────────────────────
# Multimodal formatting (read-only part of the host service)
# ────────────────────────────────────────────────────────────────


class MultimodalFormatterAdapter:
    """Turn remote-URL file inputs into provider-native chat content blocks."""

    def __init__(self, model_view: Mapping[str, Any]) -> None:
        self._provider = str(model_view.get("provider") or "").strip().lower()
        self._input_modalities = {
            str(item).strip().lower()
            for item in (model_view.get("input_modalities") or ())
            if str(item).strip()
        }

    @property
    def uses_base64_blocks(self) -> bool:
        return self._provider in _BASE64_PROVIDERS

    async def format_files(
        self,
        files: Sequence[Mapping[str, Any]],
        *,
        document_image_recognition: bool,
        include_processing_errors: bool,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for file in files:
            kind = _file_kind(file)
            if kind not in {_IMAGE, _DOCUMENT}:
                continue
            if _needs_local_hydration(file):
                logger.info(
                    "perceptual_attachment_not_hydratable url=%s",
                    _safe_locator(file),
                )
                if include_processing_errors:
                    blocks.append(_failure_block("本地附件需由文件存储服务读取"))
                continue
            url = str(file.get("url") or "").strip()
            if not url:
                continue
            try:
                if kind == _IMAGE:
                    block = await self._format_image(file, url)
                    if block is not None:
                        blocks.append(block)
                else:
                    blocks.extend(
                        await self._format_document(
                            file, url, document_image_recognition
                        )
                    )
            except Exception as error:  # noqa: BLE001 - one attachment never fails a read
                logger.warning(
                    "perceptual_attachment_format_failed kind=%s error=%s",
                    kind,
                    error,
                    exc_info=True,
                )
                if include_processing_errors:
                    blocks.append(_failure_block(str(error), kind=kind))
        return blocks

    async def _format_image(
        self, file: Mapping[str, Any], url: str
    ) -> dict[str, Any] | None:
        if _IMAGE not in self._input_modalities:
            logger.info(
                "perceptual_image_skipped provider=%s input_modalities=%s",
                self._provider,
                sorted(self._input_modalities),
            )
            return None
        if not self.uses_base64_blocks:
            return {"type": "image_url", "image_url": {"url": url}}
        content = await self._fetch_bytes(url)
        media_type = _detect_media_type(
            content, _declared_media_type(file), fallback="image/jpeg"
        )
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(content).decode("ascii"),
            },
        }

    async def _format_document(
        self,
        file: Mapping[str, Any],
        url: str,
        document_image_recognition: bool,
    ) -> list[dict[str, Any]]:
        content = await self._fetch_bytes(url)
        mime = _detect_media_type(
            content, _declared_media_type(file), fallback="application/octet-stream"
        )
        text = _extract_document_text(content, mime, file)
        if text is None:
            logger.info(
                "perceptual_document_text_unavailable mime=%s locator=%s",
                mime,
                _safe_locator(file),
            )
            return []
        blocks = [_document_block(self.uses_base64_blocks, url, text)]
        if document_image_recognition and _IMAGE in self._input_modalities:
            _append_document_images(blocks, content, mime, self.uses_base64_blocks)
        return blocks

    async def _fetch_bytes(self, url: str) -> bytes:
        """Download one attachment; the host's 30s timeout is kept."""

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content


def _failure_block(detail: str, *, kind: str | None = None) -> dict[str, Any]:
    label = {_IMAGE: "图片", _DOCUMENT: "文档"}.get(kind or "", "文件")
    return {"type": "text", "text": f"[{label}处理失败: {detail}]"}


def _document_block(uses_base64: bool, url: str, text: str) -> dict[str, Any]:
    if uses_base64:
        payload = base64.b64encode(f"文档内容：\n{text}\n".encode()).decode("ascii")
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "text/plain",
                "data": payload,
            },
        }
    return {"type": "text", "text": f'<document url="{url}">\n{text}\n</document>'}


def _append_document_images(
    blocks: list[dict[str, Any]],
    content: bytes,
    mime: str,
    uses_base64: bool,
) -> None:
    """Append embedded document images after the extracted text block.

    The host tags the preceding text block with ``[图片 …张]`` markers and then
    adds the image part, which is exactly what the loop keeps doing.
    """

    for image in _extract_document_images(content, mime):
        placeholder = (
            f"第{image['page']}页 第{image['index'] + 1}张"
            if image["page"] > 0
            else f"第{image['index'] + 1}张"
        )
        if blocks and blocks[-1].get("type") == "text":
            blocks[-1]["text"] = f"{blocks[-1]['text']}\n[图片 {placeholder}]"
        media_type = _image_media_type(image["ext"])
        if uses_base64:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(image["bytes"]).decode("ascii"),
                    },
                }
            )
        else:
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _build_data_url(image["bytes"], media_type)},
                }
            )


# ────────────────────────────────────────────────────────────────
# File helpers (host ``multimodal_service`` read-only subset)
# ────────────────────────────────────────────────────────────────


def _file_kind(file: Mapping[str, Any]) -> str:
    raw = str(file.get("type") or "").strip().lower()
    for kind in (_IMAGE, _DOCUMENT, _AUDIO, _VIDEO):
        if raw == kind or raw.startswith(f"{kind}/") or raw.startswith(f"{kind}_"):
            return kind
    return raw


def _declared_media_type(file: Mapping[str, Any]) -> str:
    return str(file.get("file_type") or "").split(";", 1)[0].strip().lower()


def _safe_locator(file: Mapping[str, Any]) -> str:
    """A log-safe identifier; never embeds bytes or long query strings."""

    url = str(file.get("url") or "").split("?", 1)[0]
    return url[:200] or str(file.get("upload_file_id") or "")


def _needs_local_hydration(file: Mapping[str, Any]) -> bool:
    """Whether the host would have to read the bytes through file storage."""

    if str(file.get("transfer_method") or "").strip().lower() == _LOCAL_FILE:
        return True
    url = str(file.get("url") or "")
    if _PERMANENT_FILE_MARKER in urlparse(url).path:
        return True
    return bool(file.get("upload_file_id")) and _model_cannot_reach_url(url)


def _model_cannot_reach_url(url: str) -> bool:
    """Host equivalence: loopback/private/link-local/reserved literals are unreachable."""

    if not url.strip():
        return True
    host = urlparse(url.strip()).hostname
    if not host:
        return True
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        parsed = ip_address(host)
    except ValueError:
        # A domain is treated as reachable: DNS answers vary per deployment.
        return False
    return bool(
        parsed.is_loopback
        or parsed.is_private
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _detect_media_type(content: bytes, declared: str, fallback: str) -> str:
    """Content sniffing first (when ``python-magic`` is installed), declared type second."""

    try:
        import magic  # noqa: PLC0415 - optional dependency, imported lazily
    except ImportError:
        return declared or fallback
    try:
        detected = magic.from_buffer(content, mime=True)
    except Exception:  # noqa: BLE001 - sniffing must never fail the read
        detected = None
    if detected and detected != "application/octet-stream":
        return str(detected).split(";", 1)[0].strip()
    return declared or fallback


def _build_data_url(content: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"


def _extract_document_text(
    content: bytes, mime: str, file: Mapping[str, Any]
) -> str | None:
    """Extract document text; ``None`` when no available parser can read it."""

    family = _document_family(mime, file)
    if family in {"text", "csv", "json"}:
        return content.decode("utf-8", errors="replace")
    if family == "pdf":
        return _extract_pdf_text(content)
    if family == "word":
        return _extract_word_text(content)
    if family == "excel":
        return _extract_xlsx_text(content)
    return None


def _document_family(mime: str, file: Mapping[str, Any]) -> str | None:
    mime = mime.strip().lower()
    if mime in _TEXT_MIME:
        return "text"
    if mime in _PDF_MIME:
        return "pdf"
    if mime in _DOC_MIME:
        return "word"
    if mime in _XLSX_MIME:
        return "excel"
    if mime in _CSV_MIME:
        return "csv"
    if mime in _JSON_MIME:
        return "json"
    return _EXTENSION_FAMILIES.get(_file_extension(file))


def _file_extension(file: Mapping[str, Any]) -> str:
    candidate = str(file.get("name") or "") or str(file.get("url") or "")
    path = urlparse(candidate).path if "://" in candidate else candidate
    suffix = PurePosixPath(path).suffix.lower()
    return suffix


def _extract_pdf_text(content: bytes) -> str | None:
    reader = _pdf_reader()
    if reader is None:
        logger.info("perceptual_pdf_parser_missing pypdf/PyPDF2 not installed")
        return None
    try:
        document = reader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in document.pages)
    except Exception:  # noqa: BLE001 - a broken attachment only degrades itself
        logger.warning("perceptual_pdf_text_extraction_failed", exc_info=True)
        return None


def _pdf_reader() -> Any | None:
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - optional dependency
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # noqa: PLC0415 - optional dependency
        except ImportError:
            return None
    return PdfReader


def _extract_word_text(content: bytes) -> str | None:
    try:
        from docx import Document as DocxDocument  # noqa: PLC0415 - optional dependency
    except ImportError:
        logger.info("perceptual_word_parser_missing python-docx not installed")
        return None
    if content[:2] != b"PK":
        # Legacy OLE2 ``.doc`` needs olefile; leave it to the caller's fallback.
        return None
    try:
        document = DocxDocument(io.BytesIO(content))
    except Exception:  # noqa: BLE001
        logger.warning("perceptual_word_text_extraction_failed", exc_info=True)
        return None
    lines = [paragraph.text.strip() for paragraph in document.paragraphs]
    for doc_table in document.tables:
        for row in doc_table.rows:
            for cell in row.cells:
                lines.append(cell.text.strip())
    text = "\n".join(line for line in lines if line)
    return text or None


def _extract_xlsx_text(content: bytes) -> str | None:
    try:
        import openpyxl  # noqa: PLC0415 - optional dependency
    except ImportError:
        logger.info("perceptual_excel_parser_missing openpyxl not installed")
        return None
    if content[:2] != b"PK":
        return None
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=True
        )
    except Exception:  # noqa: BLE001
        logger.warning("perceptual_excel_text_extraction_failed", exc_info=True)
        return None
    parts: list[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"[Sheet: {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            parts.append("\t".join("" if value is None else str(value) for value in row))
    return "\n".join(parts) or None


def _extract_document_images(content: bytes, mime: str) -> list[dict[str, Any]]:
    if mime in _PDF_MIME:
        return _extract_pdf_images(content)
    if mime in _DOC_MIME or content[:2] == b"PK":
        return _extract_docx_images(content)
    return []


def _extract_pdf_images(content: bytes) -> list[dict[str, Any]]:
    try:
        import fitz  # noqa: PLC0415 - optional dependency (PyMuPDF)
    except ImportError:
        logger.info("perceptual_pdf_image_parser_missing PyMuPDF not installed")
        return []
    images: list[dict[str, Any]] = []
    try:
        document = fitz.open(stream=content, filetype="pdf")
        try:
            for page_number, page in enumerate(document, start=1):
                for index, image in enumerate(page.get_images(full=True)):
                    extracted = document.extract_image(image[0])
                    images.append(
                        {
                            "bytes": extracted["image"],
                            "ext": extracted.get("ext", "png"),
                            "page": page_number,
                            "index": index,
                        }
                    )
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - embedded images are optional enrichment
        logger.warning("perceptual_pdf_image_extraction_failed", exc_info=True)
        return []
    return images


def _extract_docx_images(content: bytes) -> list[dict[str, Any]]:
    if content[:2] != b"PK":
        return []
    images: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            media = sorted(
                name
                for name in archive.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            )
            for index, name in enumerate(media):
                images.append(
                    {
                        "bytes": archive.read(name),
                        "ext": PurePosixPath(name).suffix.lstrip(".").lower() or "png",
                        "page": 0,
                        "index": index,
                    }
                )
    except Exception:  # noqa: BLE001
        logger.warning("perceptual_docx_image_extraction_failed", exc_info=True)
        return []
    return images


def _image_media_type(extension: str) -> str:
    normalized = extension.strip().lower().lstrip(".")
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized in {"png", "gif", "webp", "bmp"}:
        return f"image/{normalized}"
    return "image/jpeg"


# ────────────────────────────────────────────────────────────────
# Perceptual model view (non-decrypted gates only)
# ────────────────────────────────────────────────────────────────


class ModelSlotViewLookup:
    """Read one ``model_configs`` row projected to the perceptual gates.

    The projection belongs to the repository layer
    (:meth:`ModelConfigRepository.get_slot_view`); this adapter only adapts the
    returned mapping into the non-decrypted model view. It never selects
    credential columns and never writes.
    """

    def __init__(self, repository: ModelConfigRepository) -> None:
        self._repository = repository

    async def load(
        self, model_id: UUID, tenant_id: UUID | None
    ) -> dict[str, Any] | None:
        return await self._repository.get_slot_view(model_id, tenant_id)


def _normalized_model_type(value: Any) -> str:
    raw = str(getattr(value, "value", value) or "").strip().lower()
    return "llm" if raw == "chat" else raw


def _runtime_model_view(row: Mapping[str, Any], tenant_id: UUID) -> dict[str, Any]:
    """Host ``ModelConfigService._runtime_model_view`` minus credentials/channels."""

    provider = str(row.get("provider") or "")
    model_view: dict[str, Any] = {
        "model_config_id": str(row.get("id") or ""),
        "model_name": str(row.get("name") or ""),
        "model_type": _normalized_model_type(row.get("type")),
        "provider": provider,
        "input_modalities": [],
        "output_modalities": [],
        "features": [],
        "tenant_id": str(tenant_id),
    }
    try:
        profile = ModelProfile.from_stored_fields(
            model_id=row.get("id"),
            tenant_id=row.get("tenant_id"),
            type=model_view["model_type"],
            provider=provider,
            input_modalities=[str(item) for item in row.get("input_modalities") or ()],
            output_modalities=[
                str(item) for item in row.get("output_modalities") or ()
            ],
            features=[str(item) for item in row.get("features") or ()],
            capabilities=[str(item) for item in row.get("capability") or ()],
            is_omni=bool(row.get("is_omni")),
        )
    except (TypeError, ValueError):
        logger.warning(
            "perceptual_model_view_profile_derivation_failed model_config_id=%s",
            model_view["model_config_id"],
            exc_info=True,
        )
        return model_view
    model_view["model_type"] = str(profile.type.value)
    model_view["input_modalities"] = [item.value for item in profile.input_modalities]
    model_view["output_modalities"] = [item.value for item in profile.output_modalities]
    model_view["features"] = [item.value for item in profile.features]
    return model_view


# ────────────────────────────────────────────────────────────────
# Gateway
# ────────────────────────────────────────────────────────────────


class ProfileModelGateway:
    """``ModelGateway`` port implementation over :class:`ModelServiceManager`."""

    def __init__(
        self,
        runtime: ProfileRuntime,
        *,
        slot_views: ModelSlotViewLookup | None = None,
        model_configs: ModelConfigRepository | None = None,
    ) -> None:
        self._model_service: ModelServiceManager = runtime.model_service
        self._slot_views = slot_views or ModelSlotViewLookup(
            ModelConfigRepository(runtime.postgres)
        )
        self._model_configs = model_configs or ModelConfigRepository(runtime.postgres)

    async def chat(self, ctx: ReadContext) -> Any:
        """The read context's LLM slot; a missing slot is a model-unavailable error."""

        config = ctx.config
        if config is None or config.llm_model_id is None:
            raise ModelUnavailableError("llm")
        return await self._model_service.chat_model(config.llm_model_id, config.tenant_id)

    async def embedding(self, ctx: ReadContext) -> Any | None:
        """Embedding client, or ``None`` when the slot is empty (semantic degrade)."""

        config = ctx.config
        if config is None or config.embedding_model_id is None:
            return None
        return _EmbeddingAdapter(
            self._model_service, config.embedding_model_id, config.tenant_id
        )

    async def rerank(self, ctx: ReadContext) -> Any | None:
        """Rerank client, or ``None`` when the slot is empty (score fusion)."""

        config = ctx.config
        if config is None or config.rerank_model_id is None:
            return None
        return _RerankAdapter(
            self._model_service, config.rerank_model_id, config.tenant_id
        )

    async def perceptual_chat(self, ctx: ReadContext, perceptual_type: int) -> Any | None:
        """Vision/audio slots ride the chat family; ``None`` when unconfigured."""

        model_id = self._perceptual_model_id(ctx, perceptual_type)
        if model_id is None:
            return None
        config = ctx.config
        if config is None:
            return None
        return await self._model_service.chat_model(model_id, config.tenant_id)

    async def perceptual_model_view(
        self, ctx: ReadContext, perceptual_type: int
    ) -> Mapping[str, Any] | None:
        """Non-decrypted capability view of the slot (provider/modality gates only)."""

        model_id = self._perceptual_model_id(ctx, perceptual_type)
        config = ctx.config
        if model_id is None or config is None:
            return None
        row = await self._slot_views.load(model_id, config.tenant_id)
        if row is None:
            logger.warning(
                "perceptual_model_view_not_found model_config_id=%s tenant_id=%s",
                model_id,
                config.tenant_id,
            )
            return None
        return _runtime_model_view(row, config.tenant_id)

    async def model_name(self, model_id: UUID, tenant_id: UUID) -> str:
        """Display name used by the community retrieval trace."""

        rows = await self._model_configs.get_models([model_id], tenant_id)
        row = rows.get(model_id)
        if row is None:
            raise LookupError(
                f"model configuration {model_id} is not visible to tenant {tenant_id}"
            )
        return str(row.get("name") or "")

    async def multimodal_formatter(
        self, ctx: ReadContext, model_view: Mapping[str, Any]
    ) -> MultimodalFormatterAdapter:
        """Formatter for remote-URL attachments of this slot.

        ``ctx`` is accepted for port symmetry; workspace-scoped file-storage
        hydration is not available in this process (see the module docstring).
        """

        del ctx
        return MultimodalFormatterAdapter(model_view)

    @staticmethod
    def _perceptual_model_id(ctx: ReadContext, perceptual_type: int) -> UUID | None:
        config = ctx.config
        if config is None:
            return None
        if perceptual_type == PERCEPTUAL_VISION:
            return config.vision_model_id
        if perceptual_type == PERCEPTUAL_AUDIO:
            return config.audio_model_id
        if perceptual_type == PERCEPTUAL_TEXT:
            return config.llm_model_id
        return None


def build_model_gateway(runtime: ProfileRuntime) -> ProfileModelGateway:
    """Composition entry point of the :class:`ModelGateway` port."""

    return ProfileModelGateway(runtime)


__all__ = [
    "PERCEPTUAL_AUDIO",
    "PERCEPTUAL_TEXT",
    "PERCEPTUAL_VISION",
    "ModelSlotViewLookup",
    "MultimodalFormatterAdapter",
    "ProfileModelGateway",
    "build_model_gateway",
]