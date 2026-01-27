"""本体提取服务层

本模块提供本体提取的业务逻辑封装,协调OntologyExtractor、OntologyValidator和OWLValidator。
包括本体提取、OWL文件导出、配置管理等功能。

Classes:
    OntologyService: 本体提取服务类,封装业务逻辑
"""

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.models.ontology_models import (
    OntologyClass,
    OntologyExtractionResponse,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.ontology_extraction import (
    OntologyExtractor,
)
from app.core.memory.utils.validation.owl_validator import OWLValidator
from app.repositories.ontology_config_repository import OntologyConfigRepository


logger = logging.getLogger(__name__)


class OntologyService:
    """本体提取服务层
    
    封装本体提取的业务逻辑,协调各个组件:
    - OntologyExtractor: 执行LLM驱动的本体提取
    - OntologyValidator: 字符串验证
    - OWLValidator: OWL语义验证
    - OntologyConfigRepository: 配置管理
    
    Attributes:
        extractor: 本体提取器实例
        owl_validator: OWL验证器实例
        db: 数据库会话
    """
    
    def __init__(
        self,
        llm_client: OpenAIClient,
        db: Session
    ):
        """初始化本体提取服务
        
        Args:
            llm_client: OpenAI客户端实例
            db: SQLAlchemy数据库会话
        """
        self.extractor = OntologyExtractor(llm_client)
        self.owl_validator = OWLValidator()
        self.db = db
        
        logger.info("OntologyService initialized")
    
    async def extract_ontology(
        self,
        scenario: str,
        domain: Optional[str] = None,
        config_name: str = "default"
    ) -> OntologyExtractionResponse:
        """执行本体提取
        
        从数据库读取配置参数,然后调用OntologyExtractor执行提取。
        
        Args:
            scenario: 场景描述文本
            domain: 可选的领域提示
            config_name: 配置名称,默认为"default"
            
        Returns:
            OntologyExtractionResponse: 提取结果
            
        Raises:
            ValueError: 场景描述为空或配置不存在
            RuntimeError: 提取过程失败
            
        Examples:
            >>> service = OntologyService(llm_client, db)
            >>> response = await service.extract_ontology(
            ...     scenario="医院管理患者记录...",
            ...     domain="Healthcare"
            ... )
            >>> len(response.classes)
            7
        """
        # 开始计时
        start_time = time.time()
        
        # 验证输入
        if not scenario or not scenario.strip():
            logger.error("Scenario description is empty")
            raise ValueError("Scenario description cannot be empty")
        
        logger.info(
            f"Starting ontology extraction service - "
            f"scenario_length={len(scenario)}, "
            f"domain={domain}, "
            f"config_name={config_name}"
        )
        
        try:
            # 步骤1: 从数据库读取配置
            logger.debug(f"Loading configuration: {config_name}")
            config_start_time = time.time()
            
            config = self._get_config_internal(config_name)
            
            if not config:
                error_msg = f"Configuration not found: {config_name}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            config_duration = time.time() - config_start_time
            logger.info(
                f"Configuration loaded in {config_duration:.3f}s - "
                f"max_classes={config['max_classes']}, "
                f"min_classes={config['min_classes']}, "
                f"enable_owl_validation={config['enable_owl_validation']}, "
                f"timeout={config.get('llm_timeout')}"
            )
            
            # 步骤2: 调用提取器执行提取
            logger.info("Calling OntologyExtractor")
            extraction_start_time = time.time()
            
            response = await self.extractor.extract_ontology_classes(
                scenario=scenario,
                domain=domain,
                max_classes=config['max_classes'],
                min_classes=config['min_classes'],
                enable_owl_validation=config['enable_owl_validation'],
                llm_temperature=config['llm_temperature'],
                llm_max_tokens=config['llm_max_tokens'],
                max_description_length=config['max_description_length'],
                timeout=config.get('llm_timeout'),
            )
            
            extraction_duration = time.time() - extraction_start_time
            total_duration = time.time() - start_time
            
            # 步骤3: 记录提取统计
            logger.info(
                f"Ontology extraction service completed - "
                f"extracted_classes={len(response.classes)}, "
                f"domain={response.domain}, "
                f"extraction_duration={extraction_duration:.2f}s, "
                f"total_duration={total_duration:.2f}s"
            )
            
            return response
            
        except ValueError:
            # 重新抛出验证错误
            total_duration = time.time() - start_time
            logger.error(
                f"Validation error after {total_duration:.2f}s",
                exc_info=True
            )
            raise
        except Exception as e:
            total_duration = time.time() - start_time
            error_msg = f"Ontology extraction failed after {total_duration:.2f}s: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
    
    async def export_owl_file(
        self,
        classes: List[OntologyClass],
        output_path: str,
        format: str = "rdfxml",
        namespace: Optional[str] = None
    ) -> str:
        """导出OWL文件
        
        将提取的本体类导出为OWL文件,支持多种格式。
        
        Args:
            classes: 本体类列表
            output_path: 输出文件路径
            format: 导出格式,可选值: "rdfxml", "turtle", "ntriples" (默认: "rdfxml")
            namespace: 可选的命名空间URI
            
        Returns:
            str: 导出的OWL文件内容
            
        Raises:
            ValueError: 类列表为空或格式不支持
            RuntimeError: 导出失败
            
        Examples:
            >>> service = OntologyService(llm_client, db)
            >>> owl_content = await service.export_owl_file(
            ...     classes=response.classes,
            ...     output_path="ontology.owl",
            ...     format="rdfxml"
            ... )
        """
        # 验证输入
        if not classes:
            logger.error("Classes list is empty")
            raise ValueError("Classes list cannot be empty")
        
        valid_formats = ["rdfxml", "turtle", "ntriples"]
        if format not in valid_formats:
            error_msg = f"Unsupported format '{format}'. Must be one of: {', '.join(valid_formats)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info(
            f"Starting OWL export - "
            f"classes_count={len(classes)}, "
            f"output_path={output_path}, "
            f"format={format}"
        )
        
        try:
            # 步骤1: 验证本体类
            logger.debug("Validating ontology classes")
            is_valid, errors, world = self.owl_validator.validate_ontology_classes(
                classes=classes,
                namespace=namespace
            )
            
            if not is_valid:
                logger.warning(
                    f"OWL validation found {len(errors)} issues during export: {errors}"
                )
                # 继续导出,但记录警告
            
            if not world:
                error_msg = "Failed to create OWL world for export"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            # 步骤2: 导出OWL文件
            logger.info(f"Exporting to {format} format")
            owl_content = self.owl_validator.export_to_owl(
                world=world,
                output_path=output_path,
                format=format
            )
            
            logger.info(
                f"OWL export completed - "
                f"output_path={output_path}, "
                f"content_length={len(owl_content)}"
            )
            
            return owl_content
            
        except Exception as e:
            error_msg = f"OWL export failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
    
    def get_config(self, config_name: str) -> Dict[str, Any]:
        """获取配置
        
        从数据库读取指定名称的配置参数。
        
        Args:
            config_name: 配置名称
            
        Returns:
            Dict[str, Any]: 配置参数字典
            
        Raises:
            ValueError: 配置不存在
            RuntimeError: 数据库操作失败
            
        Examples:
            >>> service = OntologyService(llm_client, db)
            >>> config = service.get_config("default")
            >>> config['max_classes']
            15
        """
        logger.debug(f"Getting configuration: {config_name}")
        
        try:
            config = self._get_config_internal(config_name)
            
            if not config:
                error_msg = f"Configuration not found: {config_name}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            logger.info(f"Configuration retrieved: {config_name}")
            return config
            
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get configuration: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
    
    def update_config(
        self,
        config_name: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新配置
        
        更新指定配置的参数。
        
        Args:
            config_name: 配置名称
            updates: 要更新的字段字典
            
        Returns:
            Dict[str, Any]: 更新后的配置参数字典
            
        Raises:
            ValueError: 配置不存在或更新字段无效
            RuntimeError: 数据库操作失败
            
        Examples:
            >>> service = OntologyService(llm_client, db)
            >>> updated_config = service.update_config(
            ...     config_name="default",
            ...     updates={"max_classes": 20}
            ... )
            >>> updated_config['max_classes']
            20
        """
        logger.info(f"Updating configuration: {config_name}")
        
        try:
            # 步骤1: 获取现有配置
            db_config = OntologyConfigRepository.get_by_name(
                db=self.db,
                config_name=config_name
            )
            
            if not db_config:
                error_msg = f"Configuration not found: {config_name}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            # 步骤2: 更新配置
            logger.debug(f"Updating fields: {list(updates.keys())}")
            updated_config = OntologyConfigRepository.update(
                db=self.db,
                config_id=db_config.id,
                updates=updates
            )
            
            if not updated_config:
                error_msg = f"Failed to update configuration: {config_name}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            # 步骤3: 转换为字典返回
            result = {
                "config_name": updated_config.config_name,
                "max_classes": updated_config.max_classes,
                "min_classes": updated_config.min_classes,
                "max_description_length": updated_config.max_description_length,
                "llm_temperature": updated_config.llm_temperature,
                "llm_max_tokens": updated_config.llm_max_tokens,
                "llm_timeout": updated_config.llm_timeout,
                "enable_owl_validation": updated_config.enable_owl_validation,
            }
            
            logger.info(f"Configuration updated successfully: {config_name}")
            return result
            
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to update configuration: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
    
    def _get_config_internal(self, config_name: str) -> Optional[Dict[str, Any]]:
        """内部方法: 从数据库读取配置
        
        Args:
            config_name: 配置名称
            
        Returns:
            Optional[Dict[str, Any]]: 配置字典,不存在则返回None
        """
        try:
            db_config = OntologyConfigRepository.get_by_name(
                db=self.db,
                config_name=config_name
            )
            
            if not db_config:
                return None
            
            # 转换为字典
            config = {
                "config_name": db_config.config_name,
                "max_classes": db_config.max_classes,
                "min_classes": db_config.min_classes,
                "max_description_length": db_config.max_description_length,
                "llm_temperature": db_config.llm_temperature,
                "llm_max_tokens": db_config.llm_max_tokens,
                "llm_timeout": db_config.llm_timeout,
                "enable_owl_validation": db_config.enable_owl_validation,
            }
            
            return config
            
        except Exception as e:
            logger.error(f"Failed to read configuration from database: {str(e)}")
            raise
