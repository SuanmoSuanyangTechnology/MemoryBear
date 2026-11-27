"""The original generation model-configuration rules over profile repositories."""

import logging
import time
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from src.repositories.implicit_memory_generation import ImplicitProfileGenerationRepository
from src.repositories.model_config import ModelConfigRepository
from src.services.workspace import WorkspaceService

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Base exception for configuration-related errors.

    This exception includes context information to help with debugging
    and provides detailed error messages for different failure scenarios.
    """

    def __init__(
        self,
        message: str,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
        context: dict[str, Any] | None = None,
    ):
        """Initialize configuration error with context.

        Args:
            message: Error message describing the failure
            config_id: Optional configuration ID for context
            workspace_id: Optional workspace ID for context
            context: Optional additional context information
        """
        self.config_id = config_id
        self.workspace_id = workspace_id
        self.context = context or {}
        self.err_message = message

        # Build detailed error message with context
        detailed_message = message
        if config_id is not None:
            detailed_message = f"Configuration {config_id}: {message}"
        if workspace_id is not None:
            detailed_message = f"{detailed_message} (workspace: {workspace_id})"

        # Add context information if available
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            detailed_message = f"{detailed_message} [Context: {context_str}]"

        super().__init__(detailed_message)


class ModelNotFoundError(ConfigurationError):
    """Raised when a required model does not exist."""

    def __init__(
        self,
        model_id: str | UUID,
        model_type: str,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
        message: str | None = None,
    ):
        if message is None:
            message = f"{model_type.title()} model {model_id} not found in database"

        context = {
            "model_id": str(model_id),
            "model_type": model_type,
            "failure_type": "not_found",
        }
        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


class ModelInactiveError(ConfigurationError):
    """Raised when a required model exists but is inactive."""

    def __init__(
        self,
        model_id: str | UUID,
        model_name: str,
        model_type: str,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
        message: str | None = None,
    ):
        if message is None:
            message = f"{model_type.title()} model {model_id} ({model_name}) is inactive"

        context = {
            "model_id": str(model_id),
            "model_name": model_name,
            "model_type": model_type,
            "failure_type": "inactive",
        }
        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


class InvalidConfigError(ConfigurationError):
    """Raised when configuration validation fails."""

    def __init__(
        self,
        message: str,
        field_name: str | None = None,
        invalid_value: Any | None = None,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
    ):
        context = {}
        if field_name is not None:
            context["field_name"] = field_name
        if invalid_value is not None:
            context["invalid_value"] = str(invalid_value)
            context["invalid_value_type"] = type(invalid_value).__name__

        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


def _parse_model_id(
    model_id: str | UUID | None,
    model_type: str,
    config_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> UUID | None:
    """Parse model ID from string or UUID."""
    if model_id is None:
        return None
    if isinstance(model_id, UUID):
        return model_id
    if isinstance(model_id, str):
        if not model_id.strip():
            return None
        try:
            return UUID(model_id.strip())
        except ValueError:
            raise InvalidConfigError(  # noqa: B904 - Keep the legacy exception context.
                f"Invalid UUID format for {model_type} model ID: '{model_id}'",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id,
                config_id=config_id,
                workspace_id=workspace_id,
            )
    raise InvalidConfigError(
        f"Invalid type for {model_type} model ID: expected str or UUID, "
        f"got {type(model_id).__name__}",
        field_name=f"{model_type}_model_id",
        invalid_value=model_id,
        config_id=config_id,
        workspace_id=workspace_id,
    )


async def validate_model_exists_and_active(
    model_id: UUID,
    model_type: str,
    repository: ModelConfigRepository,
    tenant_id: UUID | None = None,
    config_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> tuple[str, bool]:
    """Validate that a model exists and is active.

    This function performs tenant-aware model validation with detailed error messages:
    - If model doesn't exist at all: "Model not found"
    - If model exists but belongs to different tenant:
      "Model belongs to different tenant" with details
    - If model exists and accessible but inactive: "Model is inactive"

    Args:
        model_id: Model UUID to validate
        model_type: Type of model ("llm", "embedding", "rerank")
        db: Database session
        tenant_id: Optional tenant ID for filtering
        config_id: Optional configuration ID for error context
        workspace_id: Optional workspace ID for error context

    Returns:
        Tuple of (model_name, is_active)

    Raises:
        ModelNotFoundError: If model does not exist or belongs to different tenant
        ModelInactiveError: If model exists but is inactive
    """

    start_time = time.time()

    try:
        # OPTIMIZED: Single query with tenant filter
        # We'll check tenant mismatch in the error handling
        row = await repository.get_slot_view(model_id, tenant_id)
        model = SimpleNamespace(**row) if row is not None else None
        elapsed_ms = (time.time() - start_time) * 1000

        if not model:
            # Model not found with tenant filter - check if it exists without filter
            unscoped = await repository.get_slot_view(model_id, tenant_id=None)
            model_without_tenant = SimpleNamespace(**unscoped) if unscoped is not None else None

            if model_without_tenant:
                # Model exists but belongs to different tenant
                logger.warning(
                    "Model belongs to different tenant",
                    extra={
                        "model_id": str(model_id),
                        "model_type": model_type,
                        "model_name": model_without_tenant.name,
                        "model_tenant_id": str(model_without_tenant.tenant_id),
                        "requested_tenant_id": str(tenant_id),
                        "is_public": model_without_tenant.is_public,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                raise ModelNotFoundError(
                    model_id=model_id,
                    model_type=model_type,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    message=(
                        f"{model_type.title()} model {model_id} ({model_without_tenant.name}) "
                        "belongs to a different tenant "
                        f"(model tenant: {model_without_tenant.tenant_id}, "
                        f"workspace tenant: {tenant_id}). "
                        "The model is not public and cannot be accessed from this workspace."
                    ),
                )
            else:
                # Model doesn't exist at all
                logger.warning(
                    "Model not found",
                    extra={
                        "model_id": str(model_id),
                        "model_type": model_type,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                raise ModelNotFoundError(
                    model_id=model_id,
                    model_type=model_type,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    message=f"{model_type.title()} model {model_id} not found",
                )

        if not model.is_active:
            logger.warning(
                "Model inactive",
                extra={
                    "model_id": str(model_id),
                    "model_name": model.name,
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise ModelInactiveError(
                model_id=model_id,
                model_name=model.name,
                model_type=model_type,
                config_id=config_id,
                workspace_id=workspace_id,
                message=f"{model_type.title()} model {model_id} ({model.name}) is inactive",
            )

        logger.debug(
            "Model validation successful",
            extra={"model_id": str(model_id), "model_name": model.name, "elapsed_ms": elapsed_ms},
        )
        return model.name, model.is_active

    except (ModelNotFoundError, ModelInactiveError):
        raise
    except Exception as e:
        logger.error(f"Model validation failed: {e}", exc_info=True)
        raise


async def validate_and_resolve_model_id(
    model_id_str: str | UUID | None,
    model_type: str,
    repository: ModelConfigRepository,
    tenant_id: UUID | None = None,
    required: bool = False,
    config_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> tuple[UUID | None, str | None]:
    """Validate and resolve a model ID, checking existence and active status.

    Returns:
        Tuple of (validated_uuid, model_name) or (None, None) if not required and empty
    """
    if model_id_str is None or (isinstance(model_id_str, str) and not model_id_str.strip()):
        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model ID is required",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id_str,
                config_id=config_id,
                workspace_id=workspace_id,
            )
        return None, None

    model_uuid = _parse_model_id(model_id_str, model_type, config_id, workspace_id)
    if model_uuid is None:
        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model ID is required",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id_str,
                config_id=config_id,
                workspace_id=workspace_id,
            )
        return None, None

    model_name, _ = await validate_model_exists_and_active(
        model_uuid, model_type, repository, tenant_id, config_id, workspace_id
    )
    return model_uuid, model_name


async def load_generation_models(runtime, workspace_id: UUID) -> tuple[UUID, UUID]:
    workspace = await WorkspaceService.from_postgres(runtime.postgres).row(workspace_id)
    if workspace is None:
        raise ConfigurationError(f"Workspace {workspace_id} not found in database")
    config_id = workspace["memory_config"]
    if config_id is None:
        raise ConfigurationError(f"空间{workspace_id}无启用的记忆配置")
    repository = ImplicitProfileGenerationRepository(runtime.postgres)
    row = await repository.config_source(config_id)
    if row is None:
        raise ConfigurationError(f"Configuration not found: config_id={config_id}")
    if row["workspace_id"] is None:
        raise ConfigurationError(f"Workspace not found for config {config_id}")
    use_preset = bool(row["is_default_config"]) and row["preset_id"] is not None
    models = ModelConfigRepository(runtime.postgres)
    resolved = {}
    for slot, family, required in (
        ("embedding", "embedding", True),
        ("llm", "llm", True),
        ("rerank", "rerank", False),
        ("vision", "llm", False),
        ("audio", "llm", False),
        ("video", "llm", False),
    ):
        model_id = row[f"preset_{slot}" if use_preset else f"workspace_{slot}"]
        resolved[slot], _ = await validate_and_resolve_model_id(
            model_id,
            family,
            models,
            row["tenant_id"],
            required=required,
            config_id=config_id,
            workspace_id=row["workspace_id"],
        )
    return resolved["llm"], row["tenant_id"]
