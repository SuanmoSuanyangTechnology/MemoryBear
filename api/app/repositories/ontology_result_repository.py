# -*- coding: utf-8 -*-
"""本体提取结果Repository模块

本模块提供ontology_extraction_result表的数据访问层,使用SQLAlchemy ORM进行数据库操作。

Classes:
    OntologyResultRepository: 本体提取结果仓储类,提供CRUD操作
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.ontology_result_model import OntologyExtractionResult
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()

TABLE_NAME = "ontology_extraction_result"


class OntologyResultRepository:
    """本体提取结果Repository

    提供ontology_extraction_result表的数据访问方法,包括CRUD操作。
    """

    @staticmethod
    def create(
        db: Session,
        scenario: str,
        domain: Optional[str],
        classes_json: dict,
        extracted_count: int
    ) -> OntologyExtractionResult:
        """创建本体提取结果记录

        Args:
            db: 数据库会话
            scenario: 场景描述文本
            domain: 领域
            classes_json: 提取的类数据(JSON格式)
            extracted_count: 提取的类数量

        Returns:
            OntologyExtractionResult: 创建的结果对象

        Raises:
            Exception: 数据库操作失败时抛出
        """
        db_logger.debug(f"创建本体提取结果: domain={domain}, count={extracted_count}")

        try:
            db_result = OntologyExtractionResult(
                scenario=scenario,
                domain=domain,
                classes_json=classes_json,
                extracted_count=extracted_count
            )
            db.add(db_result)
            db.flush()  # 获取自增ID但不提交事务

            db_logger.info(f"本体提取结果已添加到会话: ID={db_result.ontology_id}, count={extracted_count}")
            return db_result

        except Exception as e:
            db.rollback()
            db_logger.error(f"创建本体提取结果失败: {str(e)}")
            raise

    @staticmethod
    def get_by_id(db: Session, result_id: str) -> Optional[OntologyExtractionResult]:
        """根据ID获取提取结果

        Args:
            db: 数据库会话
            result_id: 结果ID (UUID字符串)

        Returns:
            Optional[OntologyExtractionResult]: 结果对象,不存在则返回None
        """
        db_logger.debug(f"根据ID查询本体提取结果: result_id={result_id}")

        try:
            stmt = select(OntologyExtractionResult).where(
                OntologyExtractionResult.ontology_id == result_id
            )
            result = db.scalars(stmt).first()

            if result:
                db_logger.debug(f"本体提取结果查询成功: ID={result_id}")
            else:
                db_logger.debug(f"本体提取结果不存在: result_id={result_id}")

            return result

        except Exception as e:
            db_logger.error(f"根据ID查询本体提取结果失败: result_id={result_id} - {str(e)}")
            raise

    @staticmethod
    def get_all(db: Session, limit: int = 50) -> List[OntologyExtractionResult]:
        """获取所有提取结果

        Args:
            db: 数据库会话
            limit: 返回结果数量限制

        Returns:
            List[OntologyExtractionResult]: 结果列表
        """
        db_logger.debug("查询所有本体提取结果")

        try:
            stmt = select(OntologyExtractionResult).order_by(
                desc(OntologyExtractionResult.created_at)
            ).limit(limit)
            
            results = db.scalars(stmt).all()

            db_logger.debug(f"本体提取结果列表查询成功: 数量={len(results)}")
            return list(results)

        except Exception as e:
            db_logger.error(f"查询所有本体提取结果失败: {str(e)}")
            raise

    @staticmethod
    def delete(db: Session, result_id: str) -> bool:
        """删除提取结果

        Args:
            db: 数据库会话
            result_id: 结果ID (UUID字符串)

        Returns:
            bool: 删除成功返回True,结果不存在返回False
        """
        db_logger.debug(f"删除本体提取结果: result_id={result_id}")

        try:
            stmt = select(OntologyExtractionResult).where(
                OntologyExtractionResult.ontology_id == result_id
            )
            db_result = db.scalars(stmt).first()

            if not db_result:
                db_logger.warning(f"本体提取结果不存在: result_id={result_id}")
                return False

            db.delete(db_result)
            db.commit()

            db_logger.info(f"本体提取结果删除成功: result_id={result_id}")
            return True

        except Exception as e:
            db.rollback()
            db_logger.error(f"删除本体提取结果失败: result_id={result_id} - {str(e)}")
            raise
