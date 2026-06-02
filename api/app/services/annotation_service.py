import uuid
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.models.base import RedBearModelConfig
from app.core.models.embedding import RedBearEmbeddings
from app.models.annotation_model import AppAnnotation, AppAnnotationSetting
from app.repositories.annotation_repository import AnnotationRepository
from app.schemas import annotation_schema

logger = get_business_logger()


class AnnotationService:
    """标注服务"""

    def __init__(self, db: Session):
        self.db = db
        self.repo = AnnotationRepository(db)

    # ==================== Annotation CRUD ====================

    def create_annotation(self, app_id: uuid.UUID, workspace_id: uuid.UUID, created_by: uuid.UUID,
                         question: str, answer: str, embedding: Optional[List[float]] = None) -> AppAnnotation:
        """创建标注"""
        return self.repo.create(app_id, workspace_id, created_by, question, answer, embedding)

    def get_annotation(self, annotation_id: uuid.UUID) -> Optional[AppAnnotation]:
        """获取标注详情"""
        return self.repo.get_by_id(annotation_id)

    def list_annotations(self, app_id: uuid.UUID, search: Optional[str] = None,
                        page: int = 1, pagesize: int = 20) -> Tuple[List[AppAnnotation], int]:
        """获取标注列表"""
        return self.repo.list_by_app(app_id, search, page, pagesize)

    def update_annotation(self, annotation_id: uuid.UUID, question: Optional[str] = None,
                         answer: Optional[str] = None, embedding: Optional[List[float]] = None) -> Optional[AppAnnotation]:
        """更新标注"""
        return self.repo.update(annotation_id, question, answer, embedding)

    def delete_annotation(self, annotation_id: uuid.UUID) -> bool:
        """删除标注"""
        return self.repo.delete(annotation_id)

    def batch_import(self, app_id: uuid.UUID, workspace_id: uuid.UUID, created_by: uuid.UUID,
                     items: List[dict]) -> dict:
        """批量导入标注"""
        return {"count": self.repo.batch_create(app_id, workspace_id, created_by, items)}

    def delete_all(self, app_id: uuid.UUID) -> dict:
        """删除应用的所有标注"""
        return {"count": self.repo.delete_all_by_app(app_id)}

    def export_all(self, app_id: uuid.UUID) -> List[AppAnnotation]:
        """导出应用的所有活跃标注"""
        return self.repo.get_all_active_by_app(app_id)

    # ==================== Hit Log ====================

    def list_hit_logs(self, annotation_id: uuid.UUID,
                      page: int = 1, pagesize: int = 20) -> Tuple[list, int]:
        return self.repo.list_hit_logs_by_annotation(annotation_id, page, pagesize)

    # ==================== Annotation Setting ====================

    def get_setting(self, app_id: uuid.UUID) -> Optional[AppAnnotationSetting]:
        """获取标注设置"""
        return self.repo.get_setting_by_app(app_id)

    def update_setting(self, app_id: uuid.UUID, workspace_id: uuid.UUID,
                      similarity_threshold: Optional[float] = None,
                      model_config_id: Optional[uuid.UUID] = None,
                      enabled: Optional[int] = None) -> AppAnnotationSetting:
        """更新标注设置"""
        return self.repo.create_or_update_setting(app_id, workspace_id, similarity_threshold, model_config_id, enabled)

    # ==================== Embedding & Similarity ====================

    def generate_embedding(self, text: str, model_config: RedBearModelConfig) -> List[float]:
        """生成文本的Embedding向量"""
        try:
            embedder = RedBearEmbeddings(model_config)
            return embedder.embed_query(text)
        except Exception as e:
            logger.error(f"生成Embedding失败: {e}")
            raise BusinessException(f"生成Embedding失败: {str(e)}", BizCode.EMBEDDING_ERROR)

    def cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """计算两个向量的余弦相似度，返回值在 [-1, 1] 范围内"""
        import math
        if not vec_a or not vec_b:
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0
        similarity = dot_product / (magnitude_a * magnitude_b)
        return max(-1.0, min(1.0, similarity))

    def find_best_match(self, query: str, annotations: List[AppAnnotation],
                       threshold: float = 0.85, model_config: Optional[RedBearModelConfig] = None,
                       app_id: Optional[uuid.UUID] = None,
                       source: str = "") -> Optional[dict]:
        """
        查找最佳匹配的标注

        Args:
            query: 用户查询
            annotations: 标注列表
            threshold: 相似度阈值
            model_config: Embedding模型配置
            app_id: 应用ID
            source: 来源（用于记录命中来源）

        Returns:
            最佳匹配的标注信息，如果没有匹配的则返回None
        """
        if not annotations:
            return None

        if not model_config:
            return None

        try:
            # 生成查询的Embedding
            query_embedding = self.generate_embedding(query, model_config)

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
