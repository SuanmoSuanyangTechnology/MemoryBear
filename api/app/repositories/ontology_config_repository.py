# -*- coding: utf-8 -*-
"""本体提取配置Repository模块

本模块提供ontology_extraction_config表的数据访问层,使用SQLAlchemy ORM进行数据库操作。
包括CRUD操作。

Classes:
    OntologyConfigRepository: 本体提取配置仓储类,提供CRUD操作
"""

from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.ontology_config_model import OntologyExtractionConfig
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()

TABLE_NAME = "ontology_extraction_config"


class OntologyConfigRepository:
    """本体提取配置Repository

    提供ontology_extraction_config表的数据访问方法,包括CRUD操作。
    """

    @staticmethod
    def create(
        db: Session,
        config_name: str,
        max_classes: int = 15,
        min_classes: int = 5,
        max_description_length: int = 500,
        llm_temperature: float = 0.3,
        llm_max_tokens: int = 2000,
        enable_owl_validation: bool = True
    ) -> OntologyExtractionConfig:
        """创建本体提取配置

        Args:
            db: 数据库会话
            config_name: 配置名称
            max_classes: 最大提取类数量
            min_classes: 最小提取类数量
            max_description_length: 描述最大字符数
            llm_temperature: LLM温度参数
            llm_max_tokens: LLM最大token数
            enable_owl_validation: 是否启用OWL验证

        Returns:
            OntologyExtractionConfig: 创建的配置对象

        Raises:
            Exception: 数据库操作失败时抛出
        """
        db_logger.debug(f"创建本体提取配置: config_name={config_name}")

        try:
            db_config = OntologyExtractionConfig(
                config_name=config_name,
                max_classes=max_classes,
                min_classes=min_classes,
                max_description_length=max_description_length,
                llm_temperature=llm_temperature,
                llm_max_tokens=llm_max_tokens,
                enable_owl_validation=enable_owl_validation
            )
            db.add(db_config)
            db.flush()  # 获取自增ID但不提交事务

            db_logger.info(f"本体提取配置已添加到会话: {db_config.config_name} (ID: {db_config.id})")
            return db_config

        except Exception as e:
            db.rollback()
            db_logger.error(f"创建本体提取配置失败: {config_name} - {str(e)}")
            raise

    @staticmethod
    def get_by_name(db: Session, config_name: str) -> Optional[OntologyExtractionConfig]:
        """根据配置名称获取配置

        Args:
            db: 数据库会话
            config_name: 配置名称

        Returns:
            Optional[OntologyExtractionConfig]: 配置对象,不存在则返回None
        """
        db_logger.debug(f"根据名称查询本体提取配置: config_name={config_name}")

        try:
            stmt = select(OntologyExtractionConfig).where(
                OntologyExtractionConfig.config_name == config_name
            )
            config = db.scalars(stmt).first()

            if config:
                db_logger.debug(f"本体提取配置查询成功: {config.config_name} (ID: {config.id})")
            else:
                db_logger.debug(f"本体提取配置不存在: config_name={config_name}")

            return config

        except Exception as e:
            db_logger.error(f"根据名称查询本体提取配置失败: config_name={config_name} - {str(e)}")
            raise

    @staticmethod
    def get_by_id(db: Session, config_id: int) -> Optional[OntologyExtractionConfig]:
        """根据ID获取配置

        Args:
            db: 数据库会话
            config_id: 配置ID

        Returns:
            Optional[OntologyExtractionConfig]: 配置对象,不存在则返回None
        """
        db_logger.debug(f"根据ID查询本体提取配置: config_id={config_id}")

        try:
            stmt = select(OntologyExtractionConfig).where(
                OntologyExtractionConfig.id == config_id
            )
            config = db.scalars(stmt).first()

            if config:
                db_logger.debug(f"本体提取配置查询成功: {config.config_name} (ID: {config_id})")
            else:
                db_logger.debug(f"本体提取配置不存在: config_id={config_id}")

            return config

        except Exception as e:
            db_logger.error(f"根据ID查询本体提取配置失败: config_id={config_id} - {str(e)}")
            raise

    @staticmethod
    def get_all(db: Session) -> List[OntologyExtractionConfig]:
        """获取所有配置

        Args:
            db: 数据库会话

        Returns:
            List[OntologyExtractionConfig]: 配置列表
        """
        db_logger.debug("查询所有本体提取配置")

        try:
            stmt = select(OntologyExtractionConfig).order_by(
                desc(OntologyExtractionConfig.updated_at)
            )
            configs = db.scalars(stmt).all()

            db_logger.debug(f"本体提取配置列表查询成功: 数量={len(configs)}")
            return list(configs)

        except Exception as e:
            db_logger.error(f"查询所有本体提取配置失败: {str(e)}")
            raise

    @staticmethod
    def update(
        db: Session,
        config_id: int,
        updates: Dict[str, Any]
    ) -> Optional[OntologyExtractionConfig]:
        """更新配置

        Args:
            db: 数据库会话
            config_id: 配置ID
            updates: 更新字段字典

        Returns:
            Optional[OntologyExtractionConfig]: 更新后的配置对象,不存在则返回None

        Raises:
            ValueError: 没有字段需要更新时抛出
        """
        db_logger.debug(f"更新本体提取配置: config_id={config_id}")

        try:
            stmt = select(OntologyExtractionConfig).where(
                OntologyExtractionConfig.id == config_id
            )
            db_config = db.scalars(stmt).first()

            if not db_config:
                db_logger.warning(f"本体提取配置不存在: config_id={config_id}")
                return None

            # 允许更新的字段
            allowed_fields = {
                'config_name', 'max_classes', 'min_classes',
                'max_description_length', 'llm_temperature',
                'llm_max_tokens', 'enable_owl_validation'
            }

            has_update = False
            for field, value in updates.items():
                if field in allowed_fields and value is not None:
                    setattr(db_config, field, value)
                    has_update = True

            if not has_update:
                raise ValueError("No fields to update")

            db.commit()
            db.refresh(db_config)

            db_logger.info(f"本体提取配置更新成功: {db_config.config_name} (ID: {config_id})")
            return db_config

        except Exception as e:
            db.rollback()
            db_logger.error(f"更新本体提取配置失败: config_id={config_id} - {str(e)}")
            raise

    @staticmethod
    def delete(db: Session, config_id: int) -> bool:
        """删除配置

        Args:
            db: 数据库会话
            config_id: 配置ID

        Returns:
            bool: 删除成功返回True,配置不存在返回False
        """
        db_logger.debug(f"删除本体提取配置: config_id={config_id}")

        try:
            stmt = select(OntologyExtractionConfig).where(
                OntologyExtractionConfig.id == config_id
            )
            db_config = db.scalars(stmt).first()

            if not db_config:
                db_logger.warning(f"本体提取配置不存在: config_id={config_id}")
                return False

            db.delete(db_config)
            db.commit()

            db_logger.info(f"本体提取配置删除成功: config_id={config_id}")
            return True

        except Exception as e:
            db.rollback()
            db_logger.error(f"删除本体提取配置失败: config_id={config_id} - {str(e)}")
            raise
