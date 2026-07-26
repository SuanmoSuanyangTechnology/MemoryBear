"""本体提取服务层

本模块提供本体提取的业务逻辑封装,协调OntologyExtractor和OWLValidator。
包括本体提取、OWL文件导出等功能。

Classes:
    OntologyService: 本体提取服务类,封装业务逻辑
"""

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.memory.models.ontology_scenario_models import (
    OntologyClass,
    OntologyExtractionResponse,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.ontology_extraction import (
    OntologyExtractor,
)
from app.core.memory.utils.validation.owl_validator import OWLValidator


logger = logging.getLogger(__name__)


class OntologyService:
    """本体提取服务层
    
    封装本体提取的业务逻辑,协调各个组件:
    - OntologyExtractor: 执行LLM驱动的本体提取
    - OWLValidator: OWL语义验证
    
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
    
    # 从环境变量获取默认语言
    from app.core.config import settings
    DEFAULT_LANGUAGE = settings.DEFAULT_LANGUAGE
    
    def __init__(
        self,
        db: Session | AsyncSession,
        llm_client=None,
    ):
        """初始化本体提取服务
        
        Args:
            llm_client: OpenAI客户端实例
            db: SQLAlchemy数据库会话 (支持同步和异步)
        """
        self.extractor = OntologyExtractor(llm_client)
        self.owl_validator = OWLValidator()
        self.db = db
        
        # 初始化Repository
        from app.repositories.ontology_scene_repository import OntologySceneRepository
        from app.repositories.ontology_class_repository import OntologyClassRepository
        
        self.scene_repo = OntologySceneRepository(db)
        self.class_repo = OntologyClassRepository(db)
        
        logger.info("OntologyService initialized")
    
    async def extract_ontology(
        self,
        scenario: str,
        domain: Optional[str] = None,
        scene_id: Optional[Any] = None,
        workspace_id: Optional[Any] = None,
        language: str = "zh"
    ) -> OntologyExtractionResponse:
        """执行本体提取
        
        使用默认配置参数调用OntologyExtractor执行提取。
        提取结果仅返回给前端，不会自动保存到数据库。
        前端需要调用 /class 接口来保存选中的类型。
        
        Args:
            scenario: 场景描述文本
            domain: 可选的领域提示
            scene_id: 可选的场景ID,用于权限验证（不再用于自动保存）
            workspace_id: 可选的工作空间ID,用于权限验证
            language: 输出语言 ("zh" 中文, "en" 英文)
            
        Returns:
            OntologyExtractionResponse: 提取结果
            
        Raises:
            ValueError: 场景描述为空、场景不存在或无权限
            RuntimeError: 提取过程失败
            
        Examples:
            >>> service = OntologyService(llm_client, db)
            >>> response = await service.extract_ontology(
            ...     scenario="医院管理患者记录...",
            ...     domain="Healthcare",
            ...     scene_id=scene_uuid,
            ...     workspace_id=workspace_uuid
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
        
        # 如果提供了scene_id,验证场景是否存在且有权限
        if scene_id and workspace_id:
            logger.info(f"Validating scene access - scene_id={scene_id}, workspace_id={workspace_id}")
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限在该场景下创建类型")
        
        logger.info(
            f"Starting ontology extraction service - "
            f"scenario_length={len(scenario)}, "
            f"domain={domain}, "
            f"scene_id={scene_id}"
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
                language=language,
            )
            
            extraction_duration = time.time() - extraction_start_time
            
            # 检查是否成功提取到类
            if not response.classes:
                logger.error("Ontology extraction failed: No classes extracted (structured output may have failed)")
                raise RuntimeError("本体提取失败：结构化输出失败，未能提取到任何本体类")
            
            # 注释：提取结果仅返回给前端，不保存到数据库
            # 前端将从返回结果中选择需要的类型，然后调用 /class 接口创建
            logger.info(
                f"Extraction completed. Classes will be saved to ontology_class "
                f"via /class endpoint based on user selection"
            )
            
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
    ) -> str:
        """导出OWL文件
        
        将提取的本体类导出为OWL文件,支持多种格式。
        
        Args:
            classes: 本体类列表
            output_path: 输出文件路径
            format: 导出格式,可选值: "rdfxml", "turtle", "ntriples" (默认: "rdfxml")
            
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

    
    # ==================== Async 版本：场景管理 ====================

    async def create_scene_async(
        self,
        scene_name: str,
        scene_description: Optional[str],
        workspace_id: Any
    ):
        """创建本体场景

        Args:
            scene_name: 场景名称
            scene_description: 场景描述（可选）
            workspace_id: 工作空间ID

        Returns:
            创建的 OntologyScene 实例

        Raises:
            ValueError: 场景名称为空
            RuntimeError: 数据库操作失败
        """
        if not scene_name or not scene_name.strip():
            logger.error("Scene name is empty")
            raise ValueError("场景名称不能为空")
        logger.info(f"Creating scene (async) - name={scene_name}, workspace_id={workspace_id}")
        try:
            scene_data = {
                "scene_name": scene_name.strip(),
                "scene_description": scene_description
            }
            scene = await self.scene_repo.create_async(scene_data, workspace_id)
            await self.db.commit()
            logger.info(f"Scene created successfully (async): {scene.scene_id}")
            return scene
        except ValueError:
            raise
        except IntegrityError as e:
            await self.db.rollback()
            logger.warning(f"Integrity error creating scene: {str(e)}")
            raise
        except Exception as e:
            await self.db.rollback()
            error_msg = f"Failed to create scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def update_scene_async(
        self,
        scene_id: Any,
        scene_name: Optional[str],
        scene_description: Optional[str],
        workspace_id: Any
    ):
        """更新本体场景

        Args:
            scene_id: 场景ID
            scene_name: 新场景名称（可选）
            scene_description: 新场景描述（可选）
            workspace_id: 工作空间ID

        Returns:
            更新后的 OntologyScene 实例

        Raises:
            ValueError: 场景不存在、无权限或名称为空
            RuntimeError: 数据库操作失败
        """
        logger.info(f"Updating scene (async): {scene_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限操作该场景")
            update_data = {}
            if scene_name is not None:
                if not scene_name.strip():
                    raise ValueError("场景名称不能为空")
                update_data["scene_name"] = scene_name.strip()
            if scene_description is not None:
                update_data["scene_description"] = scene_description
            if not update_data:
                logger.info("No update data provided, returning existing scene")
                return scene
            updated_scene = await self.scene_repo.update_async(scene_id, update_data)
            await self.db.commit()
            logger.info(f"Scene updated successfully (async): {scene_id}")
            return updated_scene
        except ValueError:
            raise
        except Exception as e:
            await self.db.rollback()
            error_msg = f"Failed to update scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def delete_scene_async(
        self,
        scene_id: Any,
        workspace_id: Any
    ) -> bool:
        """删除本体场景及其关联类型

        Args:
            scene_id: 场景ID
            workspace_id: 工作空间ID

        Returns:
            是否删除成功

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 数据库操作失败
        """
        logger.info(f"Deleting scene (async): {scene_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限操作该场景")
            success = await self.scene_repo.delete_async(scene_id)
            await self.db.commit()
            logger.info(f"Scene deleted successfully (async): {scene_id}")
            return success
        except ValueError:
            raise
        except Exception as e:
            await self.db.rollback()
            error_msg = f"Failed to delete scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def get_scene_by_id_async(
        self,
        scene_id: Any,
        workspace_id: Any
    ):
        """按ID获取场景

        Args:
            scene_id: 场景ID
            workspace_id: 工作空间ID

        Returns:
            OntologyScene 实例

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Getting scene by ID (async): {scene_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限访问该场景")
            return scene
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def get_scene_by_name_async(
        self,
        scene_name: str,
        workspace_id: Any
    ):
        """按名称获取场景

        Args:
            scene_name: 场景名称
            workspace_id: 工作空间ID

        Returns:
            OntologyScene 实例

        Raises:
            ValueError: 场景不存在
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Getting scene by name (async): {scene_name}, workspace_id: {workspace_id}")
        try:
            scene = await self.scene_repo.get_by_name_async(scene_name, workspace_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_name} in workspace {workspace_id}")
                raise ValueError("场景不存在")
            return scene
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get scene by name: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def search_scenes_by_name_async(
        self,
        keyword: str,
        workspace_id: Any
    ) -> List:
        """模糊搜索场景

        Args:
            keyword: 搜索关键词
            workspace_id: 工作空间ID

        Returns:
            匹配的场景列表

        Raises:
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Searching scenes by keyword (async): {keyword}, workspace_id: {workspace_id}")
        try:
            scenes = await self.scene_repo.search_by_name_async(keyword, workspace_id)
            logger.info(f"Found {len(scenes)} scenes matching keyword '{keyword}' in workspace {workspace_id} (async)")
            return scenes
        except Exception as e:
            error_msg = f"Failed to search scenes by keyword: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def list_scenes_async(
        self,
        workspace_id: Any,
        page: Optional[int] = None,
        page_size: Optional[int] = None
    ) -> tuple:
        """列出工作空间下的场景（支持分页）

        Args:
            workspace_id: 工作空间ID
            page: 页码（可选）
            page_size: 每页数量（可选）

        Returns:
            (场景列表, 总数) 元组

        Raises:
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Listing scenes (async) for workspace: {workspace_id}, page={page}, page_size={page_size}")
        try:
            scenes, total = await self.scene_repo.get_by_workspace_async(workspace_id, page, page_size)
            logger.info(f"Found {len(scenes)} scenes (total: {total}) in workspace {workspace_id} (async)")
            return scenes, total
        except Exception as e:
            error_msg = f"Failed to list scenes: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    # ==================== Async 版本：类型管理 ====================

    async def create_class_async(
        self,
        scene_id: Any,
        class_name: str,
        class_description: Optional[str],
        workspace_id: Any
    ):
        """创建本体类型

        Args:
            scene_id: 所属场景ID
            class_name: 类型名称
            class_description: 类型描述（可选）
            workspace_id: 工作空间ID

        Returns:
            创建的 OntologyClass 实例

        Raises:
            ValueError: 名称为空、场景不存在或无权限
            RuntimeError: 数据库操作失败
        """
        if not class_name or not class_name.strip():
            logger.error("Class name is empty")
            raise ValueError("类型名称不能为空")
        logger.info(f"Creating class (async) - name={class_name}, scene_id={scene_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("所属场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限在该场景下创建类型")
            class_data = {
                "class_name": class_name.strip(),
                "class_description": class_description
            }
            ontology_class = await self.class_repo.create_async(class_data, scene_id)
            await self.db.commit()
            logger.info(f"Class created successfully (async): {ontology_class.class_id}")
            return ontology_class
        except ValueError:
            raise
        except Exception as e:
            await self.db.rollback()
            error_msg = f"Failed to create class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def create_classes_batch_async(
        self,
        scene_id: Any,
        classes: List[Dict[str, Optional[str]]],
        workspace_id: Any
    ):
        """批量创建本体类型

        Args:
            scene_id: 所属场景ID
            classes: 类型数据列表 [{"class_name": str, "class_description": Optional[str]}, ...]
            workspace_id: 工作空间ID

        Returns:
            (成功创建的类型列表, 错误信息列表) 元组

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 数据库提交失败
        """
        logger.info(f"Batch creating classes (async) - count={len(classes)}, scene_id={scene_id}")
        scene = await self.scene_repo.get_by_id_async(scene_id)
        if not scene:
            logger.warning(f"Scene not found: {scene_id}")
            raise ValueError("所属场景不存在")
        if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
            logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
            raise ValueError("无权限在该场景下创建类型")
        created_classes = []
        errors = []
        for idx, class_data in enumerate(classes):
            class_name = class_data.get("class_name", "").strip()
            class_description = class_data.get("class_description")
            if not class_name:
                error_msg = f"第 {idx + 1} 个类型名称为空，已跳过"
                logger.warning(error_msg)
                errors.append(error_msg)
                continue
            try:
                create_data = {
                    "class_name": class_name,
                    "class_description": class_description
                }
                ontology_class = await self.class_repo.create_async(create_data, scene_id)
                created_classes.append(ontology_class)
                logger.info(f"Class created successfully (async): {class_name}")
            except Exception as e:
                error_msg = f"创建类型 '{class_name}' 失败: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        try:
            await self.db.commit()
            logger.info(f"Batch creation completed (async) - success={len(created_classes)}, failed={len(errors)}")
        except Exception as e:
            await self.db.rollback()
            error_msg = f"批量创建提交失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
        return created_classes, errors

    async def update_class_async(
        self,
        class_id: Any,
        class_name: Optional[str],
        class_description: Optional[str],
        workspace_id: Any
    ):
        """更新本体类型

        Args:
            class_id: 类型ID
            class_name: 新类型名称（可选）
            class_description: 新类型描述（可选）
            workspace_id: 工作空间ID

        Returns:
            更新后的 OntologyClass 实例

        Raises:
            ValueError: 类型不存在、无权限或名称为空
            RuntimeError: 数据库操作失败
        """
        logger.info(f"Updating class (async): {class_id}")
        try:
            ontology_class = await self.class_repo.get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_id}")
                raise ValueError("类型不存在")
            if not await self.class_repo.check_ownership_async(class_id, workspace_id):
                logger.warning(f"Permission denied - class_id={class_id}, workspace_id={workspace_id}")
                raise ValueError("无权限操作该类型")
            update_data = {}
            if class_name is not None:
                if not class_name.strip():
                    raise ValueError("类型名称不能为空")
                update_data["class_name"] = class_name.strip()
            if class_description is not None:
                update_data["class_description"] = class_description
            if not update_data:
                logger.info("No update data provided, returning existing class")
                return ontology_class
            updated_class = await self.class_repo.update_async(class_id, update_data)
            await self.db.commit()
            logger.info(f"Class updated successfully (async): {class_id}")
            return updated_class
        except ValueError:
            raise
        except Exception as e:
            await self.db.rollback()
            error_msg = f"Failed to update class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def delete_class_async(
        self,
        class_id: Any,
        workspace_id: Any
    ) -> bool:
        """删除本体类型

        Args:
            class_id: 类型ID
            workspace_id: 工作空间ID

        Returns:
            是否删除成功

        Raises:
            ValueError: 类型不存在或无权限
            RuntimeError: 数据库操作失败
        """
        logger.info(f"Deleting class (async): {class_id}")
        try:
            ontology_class = await self.class_repo.get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_id}")
                raise ValueError("类型不存在")
            if not await self.class_repo.check_ownership_async(class_id, workspace_id):
                logger.warning(f"Permission denied - class_id={class_id}, workspace_id={workspace_id}")
                raise ValueError("无权限操作该类型")
            success = await self.class_repo.delete_async(class_id)
            await self.db.commit()
            logger.info(f"Class deleted successfully (async): {class_id}")
            return success
        except ValueError:
            raise
        except Exception as e:
            await self.db.rollback()
            error_msg = f"Failed to delete class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def get_class_by_id_async(
        self,
        class_id: Any,
        workspace_id: Any
    ):
        """按ID获取本体类型

        Args:
            class_id: 类型ID
            workspace_id: 工作空间ID

        Returns:
            OntologyClass 实例

        Raises:
            ValueError: 类型不存在或无权限
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Getting class by ID (async): {class_id}")
        try:
            ontology_class = await self.class_repo.get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_id}")
                raise ValueError("类型不存在")
            if not await self.class_repo.check_ownership_async(class_id, workspace_id):
                logger.warning(f"Permission denied - class_id={class_id}, workspace_id={workspace_id}")
                raise ValueError("无权限访问该类型")
            return ontology_class
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def get_class_by_name_async(
        self,
        class_name: str,
        scene_id: Any,
        workspace_id: Any
    ):
        """按名称获取本体类型

        Args:
            class_name: 类型名称
            scene_id: 所属场景ID
            workspace_id: 工作空间ID

        Returns:
            OntologyClass 实例

        Raises:
            ValueError: 场景不存在、无权限或类型不存在
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Getting class by name (async): {class_name}, scene_id: {scene_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限访问该场景")
            ontology_class = await self.class_repo.get_by_name_async(class_name, scene_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_name} in scene {scene_id}")
                raise ValueError("类型不存在")
            return ontology_class
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get class by name: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def search_classes_by_name_async(
        self,
        keyword: str,
        scene_id: Any,
        workspace_id: Any
    ) -> List:
        """模糊搜索本体类型

        Args:
            keyword: 搜索关键词
            scene_id: 所属场景ID
            workspace_id: 工作空间ID

        Returns:
            匹配的类型列表

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Searching classes by keyword (async): {keyword}, scene_id: {scene_id}, workspace_id: {workspace_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限访问该场景")
            classes = await self.class_repo.search_by_name_async(keyword, scene_id)
            logger.info(f"Found {len(classes)} classes matching keyword '{keyword}' in scene {scene_id} (async)")
            return classes
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to search classes by keyword: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    async def list_classes_by_scene_async(
        self,
        scene_id: Any,
        workspace_id: Any
    ) -> List:
        """列出场景下的所有本体类型

        Args:
            scene_id: 场景ID
            workspace_id: 工作空间ID

        Returns:
            类型列表

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 数据库操作失败
        """
        logger.debug(f"Listing classes (async) for scene: {scene_id}")
        try:
            scene = await self.scene_repo.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")
            if not await self.scene_repo.check_ownership_async(scene_id, workspace_id):
                logger.warning(f"Permission denied - scene_id={scene_id}, workspace_id={workspace_id}")
                raise ValueError("无权限访问该场景的类型")
            classes = await self.class_repo.get_classes_by_scene_async(scene_id)
            logger.info(f"Found {len(classes)} classes in scene {scene_id} (async)")
            return classes
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to list classes: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
