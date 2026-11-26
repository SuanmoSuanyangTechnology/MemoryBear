"""Independent migration oracle, anchored to the pre-expand consumers.

Do not use FieldSpec defaults or conversion functions here: an incorrect registry must
fail verification. These reads mirror naive.py, block.py, preview.py, context.py,
document_processing.py and knowledge_sync.py. This module never drives business work.
"""

from collections.abc import Mapping
from typing import Any

from ..rag.chunk.context import is_direct_image_vision_enabled, is_embedded_image_vision_enabled
from ..rag.knowledge_graph.config import is_graph_enabled, resolve_graph_pipeline
from ..rag.parser_config import resolve_layout_recognize
from ..rag.wiki.config import read_wiki_config
from .entities import Configuration, KnowledgeConfiguration
from .fields import Scope


def _legacy_parent_child_mode(data: Mapping[str, Any], scope: Scope) -> bool:
    # Keep the pre-migration model semantics independent of configuration-backed models.
    if scope == Scope.KNOWLEDGE:
        if "auto_questions" not in data and "parent_chunk_mode" not in data:
            return False
        if (
            "auto_questions" in data
            and "parent_chunk_mode" not in data
            and not data.get("parent_child_mode", False)
        ):
            return False
        return True
    if "parent_child_mode" in data:
        value = data.get("parent_child_mode")
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "y", "on"}
        return bool(value)
    return data.get("parent_chunk_mode") in ["paragraph", "full-doc"]


def legacy_runtime(data: Mapping[str, Any] | None, scope: Scope) -> dict[str, Any]:
    data = dict(data or {})
    parent_child = _legacy_parent_child_mode(data, scope)
    image = data.get("image")
    image = image if isinstance(image, dict) else {}
    result = {
        "layout_recognize": resolve_layout_recognize(data),
        # Compatibility-only fields have no active consumer in this service.
        "html4excel": (
            str(data.get("html4excel", False)).strip().lower() in {"1", "true", "yes", "on"}
        ),
        "auto_keywords": int(data.get("auto_keywords", 0)),
        "analyze_hyperlink": bool(data.get("analyze_hyperlink", False)),
        "hyperlink_urls": bool(data.get("hyperlink_urls", False)),
        "image": {
            "vision_enabled": is_direct_image_vision_enabled(data),
            "vision_mode": image.get("vision_mode", 1),
        },
        "embedded_image": {"vision_enabled": is_embedded_image_vision_enabled(data)},
        "chunk_token_num": int(data.get("chunk_token_num", 128)),
        "chunk_overlap": int(data.get("chunk_overlap", 0) or 0),
        "delimiter": data.get("delimiter", "\n!?。；！？"),
        "parent_child_mode": parent_child,
        "parent_chunk_mode": data.get("parent_chunk_mode", "paragraph"),
        "parent_chunk_token_num": int(data.get("parent_chunk_token_num", 1024)),
        "parent_chunk_delimiter": data.get("parent_chunk_delimiter", "\n\n"),
        "auto_questions": int(data.get("auto_questions", 0) or 0),
        "qa_prompt": data.get("qa_prompt"),
    }
    if scope == Scope.DOCUMENT:
        result["doc_type"] = data.get("doc_type")
        return result
    graph = data.get("graphrag", {})
    wiki = read_wiki_config(data)
    result["graphrag"] = {
        "use_graphrag": is_graph_enabled(data),
        "scene_name": str(graph.get("scene_name") or ""),
        "entity_types": [
            str(item).strip() for item in (graph.get("entity_types") or []) if str(item).strip()
        ],
        "method": graph.get("method", "general"),
        "resolution": graph.get("resolution", True),
        "community": graph.get("community", True),
        "pipeline": resolve_graph_pipeline(data).value,
    }
    result["wiki"] = {
        "enabled": wiki.enabled,
        "language": wiki.language,
        "extract_prompt": wiki.extract_prompt,
    }
    return result


def runtime_differences(
    data: Mapping[str, Any] | None, config: Configuration, runtime: dict[str, Any]
) -> list[str]:
    """Compare every effective parsing key, plus every raw delimiter consumer."""
    expected = legacy_runtime(data, config.scope)
    differences: list[str] = []

    def compare(old: Mapping[str, Any], new: Mapping[str, Any], prefix: str = "") -> None:
        for key, value in old.items():
            path = prefix + key
            actual = new.get(key)
            if isinstance(value, dict) and isinstance(actual, dict):
                compare(value, actual, path + ".")
            elif path == "auto_questions" and config.chunk_method == "parent_child" and actual == 0:
                continue
            elif type(value) is not type(actual) or value != actual:
                differences.append(path)

    compare(expected, runtime)
    raw = data or {}
    # preview.py uses raw truthiness plus an unregistered historical alias;
    # context.py uses raw truthiness when no explicit output mode was supplied.
    old_preview = bool(raw.get("parent_child_mode") or raw.get("chunk_mode") == "parent_child")
    new_preview = bool(
        runtime.get("parent_child_mode") or runtime.get("chunk_mode") == "parent_child"
    )
    if old_preview != new_preview:
        differences.append("preview.parent_child_mode")
    if bool(raw.get("parent_child_mode", False)) != bool(runtime.get("parent_child_mode", False)):
        differences.append("context.parent_child_mode")
    # Preview/block consumers take the exact string without the naive default.
    for key in ("delimiter", "parent_chunk_delimiter"):
        if raw.get(key) != runtime.get(key):
            differences.append(f"raw_consumer.{key}")
    if isinstance(config, KnowledgeConfiguration):
        if config.source == "website":
            defaults = {
                "entry_url": "",
                "max_pages": 20,
                "delay_seconds": 1.0,
                "timeout_seconds": 10,
                "user_agent": "KnowledgeBaseCrawler/1.0",
            }
            for key, default in defaults.items():
                if raw.get(key, default) != runtime.get(key):
                    differences.append(key)
        elif config.source == "third_party":
            keys = (
                ("yuque_user_id", "yuque_token")
                if config.third_party_platform == "yuque"
                else (
                    "feishu_app_id",
                    "feishu_app_secret",
                    "feishu_folder_token",
                )
            )
            for key in keys:
                if raw.get(key, "") != runtime.get(key):
                    differences.append(key)
    return sorted(set(differences))
