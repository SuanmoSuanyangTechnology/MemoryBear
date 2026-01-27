"""本体提取服务层

本模块提供本体提取的业务逻辑封装,协调OntologyExtractor和OWLValidator。
包括本体提取、OWL文件导出等功能。

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
from app.repositories.ontology_result_repository import OntologyResultRepository


logger = logging.getLogger(__name__)


class OntologyService:
    """本体提取服务层
    
    封装本体提取的业务逻辑,协调各个组件:
    - OntologyExtractor: 执行LLM驱动的本体提取
    - OWLValidator: OWL语义验证
    - OntologyResultRepository: 结果存储
    
    Attributes:
        extractor: 本体提取器实例
        owl_validator: OWL验证器实例
        db: 数据库会话
    """
    
    # 默认配置参数
    DEFAULT_MAX_CLASSES = 15
    DEFAULT_MIN_CLASSES = 5
    DEFAULT_MAX_DESCRIPTION_LENGTH = 500
    DEFAULT_LLM_TEMPERATURE = 0.3
    DEFAULT_LLM_MAX_TOKENS = 2000
    DEFAULT_LLM_TIMEOUT = 30.0
    DEFAULT_ENABLE_OWL_VALIDATION = True
    
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
        domain: Optional[str] = None
    ) -> OntologyExtractionResponse:
        """执行本体提取
        
        使用默认配置参数调用OntologyExtractor执行提取,并将结果保存到数据库。
        
        Args:
            scenario: 场景描述文本
            domain: 可选的领域提示
            
        Returns:
            OntologyExtractionResponse: 提取结果
            
        Raises:
            ValueError: 场景描述为空
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
            f"domain={domain}"
        )
        
        try:
            # 调用提取器执行提取(使用默认配置)
            logger.info("Calling OntologyExtractor with default config")
            extraction_start_time = time.time()
            
            response = await self.extractor.extract_ontology_classes(
                scenario=scenario,
                domain=domain,
                max_classes=self.DEFAULT_MAX_CLASSES,
                min_classes=self.DEFAULT_MIN_CLASSES,
                enable_owl_validation=self.DEFAULT_ENABLE_OWL_VALIDATION,
                llm_temperature=self.DEFAULT_LLM_TEMPERATURE,
                llm_max_tokens=self.DEFAULT_LLM_MAX_TOKENS,
                max_description_length=self.DEFAULT_MAX_DESCRIPTION_LENGTH,
                timeout=self.DEFAULT_LLM_TIMEOUT,
            )
            
            extraction_duration = time.time() - extraction_start_time
            
            # 保存提取结果到数据库
            try:
                logger.debug("Saving extraction result to database")
                classes_json = {
                    "classes": [cls.model_dump() for cls in response.classes]
                }
                
                OntologyResultRepository.create(
                    db=self.db,
                    scenario=scenario,
                    domain=response.domain,
                    namespace=response.namespace,
                    classes_json=classes_json,
                    extracted_count=len(response.classes)
                )
                self.db.commit()
                logger.info("Extraction result saved to database")
            except Exception as e:
                logger.error(f"Failed to save extraction result: {str(e)}")
                # 不影响提取结果的返回,继续执行
            
            total_duration = time.time() - start_time
            
            # 记录提取统计
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
