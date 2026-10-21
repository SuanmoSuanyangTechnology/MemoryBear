"""Annotation service (frozen port of the host ``AnnotationService``).

Embedding generation is the one implementation change: the host called the
monolith's model transport chain, this service calls model-service directly
(the east-west dependency of the split design). Reference resolution keeps the
host semantics — no model configured or the reference not invokable yields no
vector, and callers keep the host's degrade-and-continue posture.

Two invoke transports coexist by face: the management face embeds through
``ModelInvokeClient`` (config-id calls), while similarity matching on the
runtime face embeds through ``RedBearEmbeddings`` (resolved invoke refs),
mirroring the host's runtime chain.
"""

from __future__ import annotations

import logging
import math
from uuid import UUID

from sqlalchemy.orm import Session

from ..integrations.model.invoke_backend import RemoteInvokeRef
from ..integrations.model_invoke import ModelInvokeClient
from ..model_clients.embedding import RedBearEmbeddings
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

    # ==================== Similarity Matching ====================

    @staticmethod
    def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """Cosine similarity of two vectors, clamped to [-1, 1]."""

        if not vec_a or not vec_b:
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0
        similarity = dot_product / (magnitude_a * magnitude_b)
        return max(-1.0, min(1.0, similarity))

    def find_best_match(
        self,
        query: str,
        annotations: list[AppAnnotation],
        threshold: float = 0.85,
        embedding_ref: RemoteInvokeRef | None = None,
        app_id: UUID | None = None,
        source: str = "",
    ) -> dict | None:
        """Best annotation above ``threshold``, recorded as a hit.

        Runs on the synchronous runtime path; the query embedding goes through
        the resolved invoke ref. Every failure degrades to "no match" so an
        annotation outage never breaks the turn. A match bumps the annotation
        hit count and appends a hit log entry.
        """

        if not annotations:
            return None

        if not embedding_ref:
            return None

        try:
            query_embedding = RedBearEmbeddings.for_invoke(embedding_ref).embed_query(query)

            best_match = None
            best_similarity = 0.0

            for annotation in annotations:
                if not annotation.embedding:
                    continue

                similarity = self.cosine_similarity(query_embedding, annotation.embedding)

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = annotation

            if best_match and best_similarity >= threshold:
                self.repo.increment_hit_count(best_match.id)
                self.repo.create_hit_log(
                    annotation_id=best_match.id,
                    query=query,
                    matched_question=best_match.question,
                    answer=best_match.answer,
                    similarity=best_similarity,
                    app_id=app_id or best_match.app_id,
                    source=source,
                )
                return {
                    "annotation_id": str(best_match.id),
                    "question": best_match.question,
                    "answer": best_match.answer,
                    "similarity": best_similarity,
                }

            return None

        except Exception as e:
            logger.error(f"标注匹配失败: {e}")
            return None

    @staticmethod
    def find_context_candidates(
        query: str,
        annotations: list[AppAnnotation],
        embedding_ref: RemoteInvokeRef | None = None,
        threshold: float = 0.6,
        top_k: int = 3,
    ) -> list[dict]:
        """Annotations usable as external context; hits are not recorded."""

        if not annotations or not embedding_ref or top_k <= 0:
            return []
        try:
            query_embedding = RedBearEmbeddings.for_invoke(embedding_ref).embed_query(query)
            candidates = []
            for annotation in annotations:
                if not annotation.embedding:
                    continue
                similarity = AnnotationService.cosine_similarity(
                    query_embedding, annotation.embedding
                )
                if similarity >= threshold:
                    candidates.append({
                        "annotation_id": str(annotation.id),
                        "question": annotation.question,
                        "answer": annotation.answer,
                        "similarity": similarity,
                        "created_at": (
                            annotation.created_at.isoformat() if annotation.created_at else None
                        ),
                        "updated_at": (
                            annotation.updated_at.isoformat() if annotation.updated_at else None
                        ),
                    })
            candidates.sort(key=lambda item: item["similarity"], reverse=True)
            return candidates[:top_k]
        except Exception as e:
            logger.error(f"标注上下文候选查询失败: {e}")
            return []


__all__ = ["AnnotationService"]
