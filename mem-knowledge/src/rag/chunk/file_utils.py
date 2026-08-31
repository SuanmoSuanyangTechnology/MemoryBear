"""Reachable hyperlink helpers for PDF and DOCX pipelines."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import requests
from docx import Document as DocxDocument
from pypdf import PdfReader


def extract_links_from_docx(docx_bytes: bytes) -> set[str]:
    links: set[str] = set()
    document = DocxDocument(BytesIO(docx_bytes))
    for relationship in document.part.rels.values():
        if relationship.reltype == (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
        ):
            links.add(relationship.target_ref)
    return links


def extract_links_from_pdf(pdf_bytes: bytes) -> set[str]:
    links: set[str] = set()
    reader = PdfReader(BytesIO(pdf_bytes))
    for page in reader.pages:
        annotations = page.get("/Annots") or []
        for annotation in annotations:
            try:
                target = annotation.get_object().get("/A")
                uri = target.get("/URI") if target else None
            except (AttributeError, KeyError, TypeError):
                continue
            if uri:
                links.add(str(uri))
    return links


def extract_html(
    url: str,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
    max_retries: int = 2,
    session=requests,
) -> tuple[bytes | None, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "final_url": url,
        "status_code": "",
        "content_type": "",
        "error_type": "",
    }
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/121.0 Safari/537.36"
        )
    }
    request_headers.update(headers or {})
    for _attempt in range(max_retries):
        try:
            response = session.get(url, timeout=timeout, headers=request_headers)
            response.raise_for_status()
            metadata.update(
                {
                    "final_url": response.url,
                    "status_code": str(response.status_code),
                    "content_type": response.headers.get("Content-Type", ""),
                }
            )
            return response.content, metadata
        except requests.RequestException as exc:
            metadata["error_type"] = type(exc).__name__
    return None, metadata


__all__ = ["extract_html", "extract_links_from_docx", "extract_links_from_pdf"]
