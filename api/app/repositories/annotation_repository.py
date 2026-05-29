import uuid
from typing import Optional, List, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models.annotation_model import AppAnnotation, AppAnnotationHitLog, AppAnnotationSetting


class AnnotationRepository:
    """标注数据仓库"""

    def __init__(self, db: Session):
        self.db = db

    # ==================== Annotation CRUD ====================

    def create(self, app_id: uuid.UUID, workspace_id: uuid.UUID, created_by: uuid.UUID,
               question: str, answer: str, embedding: Optional[List[float]] = None) -> AppAnnotation:
        """创建标注"""
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

    def get_by_id(self, annotation_id: uuid.UUID) -> Optional[AppAnnotation]:
        """根据ID获取标注"""
        return self.db.query(AppAnnotation).filter(
            AppAnnotation.id == annotation_id,
            AppAnnotation.is_active == 1
        ).first()

    def list_by_app(self, app_id: uuid.UUID, search: Optional[str] = None,
                    page: int = 1, pagesize: int = 20) -> Tuple[List[AppAnnotation], int]:
        """获取应用的标注列表"""
        query = self.db.query(AppAnnotation).filter(
            AppAnnotation.app_id == app_id,
            AppAnnotation.is_active == 1
        )

        if search:
            search_filter = or_(
                AppAnnotation.question.ilike(f"%{search}%"),
                AppAnnotation.answer.ilike(f"%{search}%")
            )
            query = query.filter(search_filter)

        total = query.count()
        items = query.order_by(AppAnnotation.created_at.desc()) \
            .offset((page - 1) * pagesize) \
            .limit(pagesize) \
            .all()
        return items, total

    def update(self, annotation_id: uuid.UUID, question: Optional[str] = None,
               answer: Optional[str] = None, embedding: Optional[List[float]] = None) -> Optional[AppAnnotation]:
        """更新标注"""
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

    def delete(self, annotation_id: uuid.UUID) -> bool:
        """软删除标注"""
        annotation = self.get_by_id(annotation_id)
        if not annotation:
            return False
        annotation.is_active = 0
        self.db.commit()
        return True

    def increment_hit_count(self, annotation_id: uuid.UUID) -> bool:
        """增加命中次数"""
        annotation = self.get_by_id(annotation_id)
        if not annotation:
            return False
        annotation.hit_count += 1
        self.db.commit()
        return True

    def get_all_active_by_app(self, app_id: uuid.UUID) -> List[AppAnnotation]:
        """获取应用的所有活跃标注"""
        return self.db.query(AppAnnotation).filter(
            AppAnnotation.app_id == app_id,
            AppAnnotation.is_active == 1
        ).all()

    def batch_create(self, app_id: uuid.UUID, workspace_id: uuid.UUID, created_by: uuid.UUID,
                     items: List[dict]) -> int:
        """批量创建标注"""
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

    def delete_all_by_app(self, app_id: uuid.UUID) -> int:
        """软删除应用的所有标注"""
        result = self.db.query(AppAnnotation).filter(
            AppAnnotation.app_id == app_id,
            AppAnnotation.is_active == 1
        ).update({"is_active": 0}, synchronize_session=False)
        self.db.commit()
        return result

    # ==================== Annotation Setting ====================

    def get_setting_by_app(self, app_id: uuid.UUID) -> Optional[AppAnnotationSetting]:
        """获取应用的标注设置"""
        return self.db.query(AppAnnotationSetting).filter(
            AppAnnotationSetting.app_id == app_id
        ).first()

    def create_or_update_setting(self, app_id: uuid.UUID, workspace_id: uuid.UUID,
                                 similarity_threshold: Optional[float] = None,
                                 model_config_id: Optional[uuid.UUID] = None,
                                 enabled: Optional[int] = None) -> AppAnnotationSetting:
        """创建或更新标注设置"""
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
        else:
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

    def create_hit_log(self, annotation_id: uuid.UUID,
                       query: str, matched_question: str, answer: str,
                       similarity: float, app_id: uuid.UUID,
                       source: str) -> AppAnnotationHitLog:
        hit_log = AppAnnotationHitLog(
            annotation_id=annotation_id,
            app_id=app_id,
            source=source,
            query=query,
            matched_question=matched_question,
            answer=answer,
            similarity=similarity,
        )
        self.db.add(hit_log)
        self.db.commit()
        self.db.refresh(hit_log)
        return hit_log

    def list_hit_logs_by_annotation(self, annotation_id: uuid.UUID,
                                     page: int = 1, pagesize: int = 20) -> Tuple[List[AppAnnotationHitLog], int]:
        query = self.db.query(AppAnnotationHitLog).filter(
            AppAnnotationHitLog.annotation_id == annotation_id
        )
        total = query.count()
        items = query.order_by(AppAnnotationHitLog.hit_at.desc()) \
            .offset((page - 1) * pagesize) \
            .limit(pagesize) \
            .all()
        return items, total

    def delete_hit_logs_by_annotation(self, annotation_id: uuid.UUID) -> int:
        result = self.db.query(AppAnnotationHitLog).filter(
            AppAnnotationHitLog.annotation_id == annotation_id
        ).delete(synchronize_session=False)
        self.db.commit()
        return result
