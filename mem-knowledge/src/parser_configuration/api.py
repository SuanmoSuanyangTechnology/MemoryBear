"""V2 configuration commands and safe, contract-specific validation diagnostics."""

from typing import Any, cast

from ..errors import KnowledgeError
from .conversion import MISSING, from_api, get_path, new_configuration
from .entities import ConfigurationError, KnowledgeConfiguration
from .fields import Scope, fields_for
from .legacy import immutable_field

TYPE_BY_SOURCE = {"space": "General", "website": "Web", "third_party": "Third-party"}


def configuration_error(error: ConfigurationError) -> KnowledgeError:
    if error.code == "immutable_field":
        return immutable_field(error.field)
    if error.code == "parent_child_qa_conflict":
        return KnowledgeError.from_code("KB_PARSER_CONFIG_PARENT_CHILD_QA_CONFLICT")
    source = error.field.startswith(("sources", "web_", "yuque_", "feishu_", "third_party_"))
    combination = error.code in {"default_with_custom_parameters", "wiki_qa_conflict"}
    status = 400 if (source or combination) and error.code != "unknown_api_field" else 422
    return KnowledgeError.from_code(
        "KB_VALIDATION_ERROR",
        status_code=status,
        diagnostic=f"processing_configuration.{error.field}: {error.code}",
    )


def create_configuration(values: dict[str, Any]) -> KnowledgeConfiguration:
    current = cast(KnowledgeConfiguration, new_configuration(Scope.KNOWLEDGE))
    if values["type"] == "Folder":
        return current.model_copy(
            update={
                "knowledge_type": "Folder",
                "source": None,
                "index_strategy": None,
            }
        )
    nested = values["processing_configuration"]
    source = values["source"]
    try:
        if get_path(nested, "defaults.chunking.method") is MISSING:
            raise ConfigurationError("required", "defaults.chunking.method")
        if get_path(nested, "graph.pipeline", "evidence") != "evidence":
            raise ConfigurationError("invalid_enum", "graph_pipeline")
        updates = {
            "knowledge_type": TYPE_BY_SOURCE[source],
            "source": source,
            "index_strategy": values["index_strategy"],
        }
        if source == "website":
            updates.update(
                {
                    spec.column: spec.legacy_default
                    for spec in fields_for(Scope.KNOWLEDGE)
                    if spec.column.startswith("web_")
                }
            )
        if source == "third_party":
            platform = get_path(nested, "sources.third_party.platform", None)
            if not isinstance(platform, str) or platform not in {"yuque", "feishu"}:
                raise ConfigurationError("invalid_platform", "third_party_platform")
            updates["third_party_platform"] = platform
        # Only the complete merged result is validated; no partial source can be saved.
        config = cast(KnowledgeConfiguration, from_api(nested, current.model_copy(update=updates)))
        config.require_complete_source()
        return config
    except ConfigurationError as exc:
        raise configuration_error(exc) from None


def patch_configuration(
    current: KnowledgeConfiguration,
    nested: dict[str, Any],
) -> KnowledgeConfiguration:
    try:
        return cast(KnowledgeConfiguration, from_api(nested, current))
    except ConfigurationError as exc:
        raise configuration_error(exc) from None


def require_sync_source(config: KnowledgeConfiguration) -> None:
    """Use existing connector errors before dispatch and again before external I/O."""
    issues = config.source_issues()
    if issues:
        credential = any(issue.field.startswith(("yuque_", "feishu_")) for issue in issues)
        raise KnowledgeError.from_code(
            "KB_CONNECTOR_CREDENTIAL_INVALID" if credential else "KB_CONNECTOR_SOURCE_INVALID",
            status_code=400,
        )
