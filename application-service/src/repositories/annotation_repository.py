"""Annotation data access (frozen port of the host ``AnnotationRepository``).

Only the paths consumed by the migrated annotation faces are ported: CRUD,
settings and hit-log reads. Hit-log writes and similarity matching belong to
the runtime plane and are not part of this face.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.annotation_model import (
    AppAnnotation,
    AppAnnotationHitLog,
    AppAnnotationSetting,
)


class AnnotationRepository:
    """Annotation repository."""

    def __init__(self, db: Session):
        self.db = db

    # ==================== Annotation CRUD ====================

    def create(
        self,
        app_id: UUID,
        workspace_id: UUID,
        created_by: UUID,
        question: str,
        answer: str,
        embedding: list[float] | None = None,
    ) -> AppAnnotation:
        """Create an annotation."""

        annotation = AppAnnotation(
            app_id=app_id,
            workspace_id=workspace_id,
            created_by=created_by,
            question=question,
            answer=answer,
            embedding=embedding,
            hit_count=0,
            is_active=1,
        )
        self.db.add(annotation)
        self.db.commit()
        self.db.refresh(annotation)
        return annotation

    def get_by_id(self, annotation_id: UUID) -> AppAnnotation | None:
        """Fetch an active annotation by id."""

        return self.db.query(AppAnnotation).filter(
            AppAnnotation.id == annotation_id,
            AppAnnotation.is_active == 1,
        ).first()

    def list_by_app(
        self,
        app_id: UUID,
        search: str | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[AppAnnotation], int]:
        """Paginated active annotations of one app."""

        query = self.db.query(AppAnnotation).filter(
            AppAnnotation.app_id == app_id,
            AppAnnotation.is_active == 1,
        )

        if search:
            query = query.filter(
                or_(
                    AppAnnotation.question.ilike(f"%{search}%"),
                    AppAnnotation.answer.ilike(f"%{search}%"),
                )
            )

        total = query.count()
        items = query.order_by(AppAnnotation.created_at.desc()).offset(
            (page - 1) * pagesize
        ).limit(pagesize).all()
        return items, total

    def update(
        self,
        annotation_id: UUID,
        question: str | None = None,
        answer: str | None = None,
        embedding: list[float] | None = None,
    ) -> AppAnnotation | None:
        """Update an annotation; None when missing."""

        annotation = self.get_by_id(annotation_id)
        if not annotation:
            return None

        if question is not None:
            annotation.question = question
        if answer is not None:
            annotation.answer = answer
        if embedding is not None:
            annotation.embedding = embedding

        self.db.commit()
        self.db.refresh(annotation)
        return annotation

    def delete(self, annotation_id: UUID) -> bool:
        """Soft-delete an annotation."""

        annotation = self.get_by_id(annotation_id)
        if not annotation:
            return False
        annotation.is_active = 0
        self.db.commit()
        return True

    def get_all_active_by_app(self, app_id: UUID) -> list[AppAnnotation]:
        """All active annotations of one app (export)."""

        return self.db.query(AppAnnotation).filter(
            AppAnnotation.app_id == app_id,
            AppAnnotation.is_active == 1,
        ).all()

    def batch_create(
        self,
        app_id: UUID,
        workspace_id: UUID,
        created_by: UUID,
        items: list[dict],
    ) -> int:
        """Bulk-create annotations, one commit for the whole batch."""

        count = 0
        for item in items:
            annotation = AppAnnotation(
                app_id=app_id,
                workspace_id=workspace_id,
                created_by=created_by,
                question=item["question"],
                answer=item["answer"],
                embedding=item.get("embedding"),
                hit_count=0,
                is_active=1,
            )
            self.db.add(annotation)
            count += 1
        self.db.commit()
        return count

    def delete_all_by_app(self, app_id: UUID) -> int:
        """Soft-delete all active annotations of one app."""

        result = self.db.query(AppAnnotation).filter(
            AppAnnotation.app_id == app_id,
            AppAnnotation.is_active == 1,
        ).update({"is_active": 0}, synchronize_session=False)
        self.db.commit()
        return result

    # ==================== Annotation Setting ====================

    def get_setting_by_app(self, app_id: UUID) -> AppAnnotationSetting | None:
        """Fetch the annotation settings of one app."""

        return self.db.query(AppAnnotationSetting).filter(
            AppAnnotationSetting.app_id == app_id
        ).first()

    def create_or_update_setting(
        self,
        app_id: UUID,
        workspace_id: UUID,
        similarity_threshold: float | None = None,
        model_config_id: UUID | None = None,
        enabled: int | None = None,
    ) -> AppAnnotationSetting:
        """Create or update the annotation settings of one app."""

        setting = self.get_setting_by_app(app_id)
        if setting:
            if similarity_threshold is not None:
                setting.similarity_threshold = similarity_threshold
            if model_config_id is not None:
                setting.model_config_id = model_config_id
            if enabled is not None:
                setting.enabled = enabled
            self.db.commit()
            self.db.refresh(setting)
            return setting

        setting = AppAnnotationSetting(
            app_id=app_id,
            workspace_id=workspace_id,
            similarity_threshold=similarity_threshold or 0.85,
            model_config_id=model_config_id,
            enabled=enabled if enabled is not None else 0,
        )
        self.db.add(setting)
        self.db.commit()
        self.db.refresh(setting)
        return setting

    # ==================== Hit Log ====================

    def list_hit_logs_by_annotation(
        self,
        annotation_id: UUID,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[AppAnnotationHitLog], int]:
        """Paginated hit history of one annotation, newest first."""

        query = self.db.query(AppAnnotationHitLog).filter(
            AppAnnotationHitLog.annotation_id == annotation_id
        )
        total = query.count()
        items = query.order_by(AppAnnotationHitLog.hit_at.desc()).offset(
            (page - 1) * pagesize
        ).limit(pagesize).all()
        return items, total


__all__ = ["AnnotationRepository"]
