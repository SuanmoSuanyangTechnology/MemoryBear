"""Annotation service (frozen port of the host ``AnnotationService``).

Embedding generation is the one implementation change: the host called the
monolith's model transport chain, this service calls model-service directly
(the east-west dependency of the split design). Reference resolution keeps the
host semantics — no model configured or the reference not invokable yields no
vector, and callers keep the host's degrade-and-continue posture.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from ..integrations.model_invoke import ModelInvokeClient
from ..models.annotation_model import AppAnnotation, AppAnnotationSetting
from ..repositories.annotation_repository import AnnotationRepository
from .model_ref_gate import resolve_invoke_ref

logger = logging.getLogger(__name__)


class AnnotationService:
    """Annotation service."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = AnnotationRepository(db)

    # ==================== Annotation CRUD ====================

    def create_annotation(
        self,
        app_id: UUID,
        workspace_id: UUID,
        created_by: UUID,
        question: str,
        answer: str,
        embedding: list[float] | None = None,
    ) -> AppAnnotation:
        return self.repo.create(app_id, workspace_id, created_by, question, answer, embedding)

    def get_annotation(self, annotation_id: UUID) -> AppAnnotation | None:
        return self.repo.get_by_id(annotation_id)

    def list_annotations(
        self,
        app_id: UUID,
        search: str | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[AppAnnotation], int]:
        return self.repo.list_by_app(app_id, search, page, pagesize)

    def update_annotation(
        self,
        annotation_id: UUID,
        question: str | None = None,
        answer: str | None = None,
        embedding: list[float] | None = None,
    ) -> AppAnnotation | None:
        return self.repo.update(annotation_id, question, answer, embedding)

    def delete_annotation(self, annotation_id: UUID) -> bool:
        return self.repo.delete(annotation_id)

    def batch_import(
        self,
        app_id: UUID,
        workspace_id: UUID,
        created_by: UUID,
        items: list[dict],
    ) -> dict:
        return {"count": self.repo.batch_create(app_id, workspace_id, created_by, items)}

    def delete_all(self, app_id: UUID) -> dict:
        return {"count": self.repo.delete_all_by_app(app_id)}

    def export_all(self, app_id: UUID) -> list[AppAnnotation]:
        return self.repo.get_all_active_by_app(app_id)

    # ==================== Hit Log ====================

    def list_hit_logs(
        self, annotation_id: UUID, page: int = 1, pagesize: int = 20
    ) -> tuple[list, int]:
        return self.repo.list_hit_logs_by_annotation(annotation_id, page, pagesize)

    # ==================== Annotation Setting ====================

    def get_setting(self, app_id: UUID) -> AppAnnotationSetting | None:
        return self.repo.get_setting_by_app(app_id)

    def update_setting(
        self,
        app_id: UUID,
        workspace_id: UUID,
        similarity_threshold: float | None = None,
        model_config_id: UUID | None = None,
        enabled: int | None = None,
    ) -> AppAnnotationSetting:
        return self.repo.create_or_update_setting(
            app_id, workspace_id, similarity_threshold, model_config_id, enabled
        )

    # ==================== Embedding ====================

    def resolve_embedding_ref(self, model_config_id: UUID | None, tenant_id: UUID):
        """Resolve the embedding model reference once per request.

        Split from the invoke itself so batch import performs a single
        reference read instead of one per row. Returns None when no model is
        configured or the reference is missing / disabled (the host's two-step
        resolution).
        """

        if model_config_id is None:
            return None
        return resolve_invoke_ref(self.db, model_config_id, tenant_id)

    def generate_embedding(
        self,
        question: str,
        *,
        ref,
        tenant_id: UUID,
        workspace_id: UUID | None,
        actor_id: UUID | None,
        client: ModelInvokeClient,
    ) -> list[float] | None:
        """Generate one question embedding through model-service.

        Pure invoke: no DB access, so batch callers can reuse the
        pre-resolved ``ref`` without N+1 reads. Invoke failures propagate for
        the caller's degrade-and-continue handling.
        """

        if ref is None:
            return None
        vectors = client.embed_texts(
            ref.id,
            [question],
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        return vectors[0] if vectors else None


__all__ = ["AnnotationService"]
