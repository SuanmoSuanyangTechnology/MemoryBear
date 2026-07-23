"""本体提取服务层

本模块提供本体提取的业务逻辑封装，协调OntologyExtractor进行LLM驱动的本体提取。

Classes:
    OntologyService: 纯异步静态方法服务类，所有方法均为 @staticmethod async
"""

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.models.ontology_scenario_models import (
    OntologyExtractionResponse,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.ontology_extraction import (
    OntologyExtractor,
)
from app.repositories.ontology_class_repository import OntologyClassRepository
from app.repositories.ontology_scene_repository import OntologySceneRepository


logger = logging.getLogger(__name__)


class OntologyService:
    """纯异步静态方法服务层
    
    所有方法均为 @staticmethod async，通过参数注入 AsyncSession db。
    协调组件:
    - OntologyExtractor: 执行LLM驱动的本体提取
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

    # ==================== 异步本体场景管理方法 ====================

    @staticmethod
    async def create_scene_async(
        db: AsyncSession,
        scene_name: str,
        scene_description: Optional[str],
        workspace_id: Any
    ):
        """创建本体场景（异步版本）

        Args:
            db: 异步数据库会话
            scene_name: 场景名称
            scene_description: 场景描述
            workspace_id: 所属工作空间ID

        Returns:
            OntologyScene: 创建的场景对象

        Raises:
            ValueError: 场景名称为空
            RuntimeError: 创建失败
        """
        if not scene_name or not scene_name.strip():
            logger.error("Scene name is empty")
            raise ValueError("场景名称不能为空")

        logger.info(
            f"Creating scene (async) - "
            f"name={scene_name}, workspace_id={workspace_id}"
        )

        try:
            scene_data = {
                "scene_name": scene_name.strip(),
                "scene_description": scene_description
            }

            scene = await OntologySceneRepository(db).create_async(scene_data, workspace_id)

            logger.info(f"Scene created successfully (async): {scene.scene_id}")

            return scene

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to create scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def update_scene_async(
        db: AsyncSession,
        scene_id: Any,
        scene_name: Optional[str],
        scene_description: Optional[str],
        workspace_id: Any
    ):
        """更新本体场景（异步版本）

        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            scene_name: 场景名称（可选）
            scene_description: 场景描述（可选）
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            OntologyScene: 更新后的场景对象

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 更新失败
        """
        logger.info(f"Updating scene (async): {scene_id}")

        try:
            # 检查场景是否存在
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            # 检查权限
            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限操作该场景")

            # 准备更新数据
            update_data = {}
            if scene_name is not None:
                if not scene_name.strip():
                    raise ValueError("场景名称不能为空")
                update_data["scene_name"] = scene_name.strip()

            if scene_description is not None:
                update_data["scene_description"] = scene_description

            # 如果没有更新数据，直接返回
            if not update_data:
                logger.info("No update data provided, returning existing scene")
                return scene

            # 执行更新
            updated_scene = await OntologySceneRepository(db).update_async(scene_id, update_data)

            logger.info(f"Scene updated successfully (async): {scene_id}")

            return updated_scene

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to update scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def delete_scene_async(
        db: AsyncSession,
        scene_id: Any,
        workspace_id: Any
    ) -> bool:
        """删除本体场景（异步版本）

        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            bool: 删除成功返回True

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 删除失败
        """
        logger.info(f"Deleting scene (async): {scene_id}")

        try:
            # 检查场景是否存在
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            # 检查权限
            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限操作该场景")

            # 执行删除
            success = await OntologySceneRepository(db).delete_async(scene_id)

            logger.info(f"Scene deleted successfully (async): {scene_id}")

            return success

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to delete scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def get_scene_by_id_async(
        db: AsyncSession,
        scene_id: Any,
        workspace_id: Any
    ):
        """获取单个场景（异步版本）

        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            Optional[OntologyScene]: 场景对象

        Raises:
            ValueError: 场景不存在或无权限
        """
        logger.debug(f"Getting scene by ID (async): {scene_id}")

        try:
            # 获取场景
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            # 检查权限
            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限访问该场景")

            return scene

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get scene: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def get_scene_by_name_async(
        db: AsyncSession,
        scene_name: str,
        workspace_id: Any
    ):
        """根据场景名称获取场景（精确匹配，异步版本）

        Args:
            db: 异步数据库会话
            scene_name: 场景名称
            workspace_id: 工作空间ID

        Returns:
            Optional[OntologyScene]: 场景对象

        Raises:
            ValueError: 场景不存在
        """
        logger.debug(f"Getting scene by name (async): {scene_name}, workspace_id: {workspace_id}")

        try:
            # 获取场景
            scene = await OntologySceneRepository(db).get_by_name_async(scene_name, workspace_id)
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

    @staticmethod
    async def search_scenes_by_name_async(
        db: AsyncSession,
        keyword: str,
        workspace_id: Any
    ) -> List:
        """根据关键词模糊搜索场景（异步版本）

        Args:
            db: 异步数据库会话
            keyword: 搜索关键词
            workspace_id: 工作空间ID

        Returns:
            List[OntologyScene]: 匹配的场景列表

        Raises:
            RuntimeError: 搜索失败
        """
        logger.debug(f"Searching scenes by keyword (async): {keyword}, workspace_id: {workspace_id}")

        try:
            scenes = await OntologySceneRepository(db).search_by_name_async(keyword, workspace_id)

            logger.info(
                f"Found {len(scenes)} scenes matching keyword '{keyword}' "
                f"in workspace {workspace_id}"
            )

            return scenes

        except Exception as e:
            error_msg = f"Failed to search scenes by keyword: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def list_scenes_async(
        db: AsyncSession,
        workspace_id: Any,
        page: Optional[int] = None,
        page_size: Optional[int] = None
    ) -> tuple:
        """获取工作空间下的所有场景（支持分页，异步版本）

        Args:
            db: 异步数据库会话
            workspace_id: 工作空间ID
            page: 页码（可选，从1开始）
            page_size: 每页数量（可选）

        Returns:
            tuple: (场景列表, 总数量)

        Raises:
            RuntimeError: 查询失败
        """
        logger.debug(f"Listing scenes (async) for workspace: {workspace_id}, page={page}, page_size={page_size}")

        try:
            scenes, total = await OntologySceneRepository(db).get_by_workspace_async(
                workspace_id, page, page_size
            )

            logger.info(f"Found {len(scenes)} scenes (total: {total}) in workspace {workspace_id}")

            return scenes, total

        except Exception as e:
            error_msg = f"Failed to list scenes: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    # ==================== 异步本体类型管理方法 ====================

    @staticmethod
    async def create_class_async(
        db: AsyncSession,
        scene_id: Any,
        class_name: str,
        class_description: Optional[str],
        workspace_id: Any
    ):
        """创建本体类型（异步版本）

        Args:
            db: 异步数据库会话
            scene_id: 所属场景ID
            class_name: 类型名称
            class_description: 类型描述
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            OntologyClass: 创建的类型对象

        Raises:
            ValueError: 类型名称为空、场景不存在或无权限
            RuntimeError: 创建失败
        """
        # 验证输入
        if not class_name or not class_name.strip():
            logger.error("Class name is empty")
            raise ValueError("类型名称不能为空")

        logger.info(
            f"Creating class (async) - "
            f"name={class_name}, scene_id={scene_id}"
        )

        try:
            # 检查场景是否存在且属于当前工作空间
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("所属场景不存在")

            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限在该场景下创建类型")

            # 创建类型
            class_data = {
                "class_name": class_name.strip(),
                "class_description": class_description
            }

            ontology_class = await OntologyClassRepository(db).create_async(class_data, scene_id)

            logger.info(f"Class created successfully (async): {ontology_class.class_id}")

            return ontology_class

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to create class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def create_classes_batch_async(
        db: AsyncSession,
        scene_id: Any,
        classes: List[Dict[str, Optional[str]]],
        workspace_id: Any
    ):
        """批量创建本体类型（异步版本）

        Args:
            db: 异步数据库会话
            scene_id: 所属场景ID
            classes: 类型列表，每个元素包含 class_name 和 class_description
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            Tuple[List, List[str]]: (成功创建的类型列表, 错误信息列表)

        Raises:
            ValueError: 场景不存在或无权限
        """
        logger.info(
            f"Batch creating classes (async) - "
            f"count={len(classes)}, scene_id={scene_id}"
        )

        # 检查场景是否存在且属于当前工作空间（只检查一次）
        scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
        if not scene:
            logger.warning(f"Scene not found: {scene_id}")
            raise ValueError("所属场景不存在")

        if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
            logger.warning(
                f"Permission denied - scene_id={scene_id}, "
                f"workspace_id={workspace_id}"
            )
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
                # 创建类型（不需要再次检查权限）
                create_data = {
                    "class_name": class_name,
                    "class_description": class_description
                }

                ontology_class = await OntologyClassRepository(db).create_async(create_data, scene_id)
                created_classes.append(ontology_class)
                logger.info(f"Class created successfully (async): {class_name}")

            except Exception as e:
                error_msg = f"创建类型 '{class_name}' 失败: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        logger.info(
            f"Batch creation completed (async) - "
            f"success={len(created_classes)}, failed={len(errors)}"
        )

        return created_classes, errors

    @staticmethod
    async def update_class_async(
        db: AsyncSession,
        class_id: Any,
        class_name: Optional[str],
        class_description: Optional[str],
        workspace_id: Any
    ):
        """更新本体类型（异步版本）

        Args:
            db: 异步数据库会话
            class_id: 类型ID
            class_name: 类型名称（可选）
            class_description: 类型描述（可选）
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            OntologyClass: 更新后的类型对象

        Raises:
            ValueError: 类型不存在或无权限
            RuntimeError: 更新失败
        """
        logger.info(f"Updating class (async): {class_id}")

        try:
            # 检查类型是否存在
            ontology_class = await OntologyClassRepository(db).get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_id}")
                raise ValueError("类型不存在")

            # 检查权限（通过场景关联）
            if not await OntologyClassRepository(db).check_ownership_async(class_id, workspace_id):
                logger.warning(
                    f"Permission denied - class_id={class_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限操作该类型")

            # 准备更新数据
            update_data = {}
            if class_name is not None:
                if not class_name.strip():
                    raise ValueError("类型名称不能为空")
                update_data["class_name"] = class_name.strip()

            if class_description is not None:
                update_data["class_description"] = class_description

            # 如果没有更新数据，直接返回
            if not update_data:
                logger.info("No update data provided, returning existing class")
                return ontology_class

            # 执行更新
            updated_class = await OntologyClassRepository(db).update_async(class_id, update_data)

            logger.info(f"Class updated successfully (async): {class_id}")

            return updated_class

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to update class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def delete_class_async(
        db: AsyncSession,
        class_id: Any,
        workspace_id: Any
    ) -> bool:
        """删除本体类型（异步版本）

        Args:
            db: 异步数据库会话
            class_id: 类型ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            bool: 删除成功返回True

        Raises:
            ValueError: 类型不存在或无权限
            RuntimeError: 删除失败
        """
        logger.info(f"Deleting class (async): {class_id}")

        try:
            # 检查类型是否存在
            ontology_class = await OntologyClassRepository(db).get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_id}")
                raise ValueError("类型不存在")

            # 检查权限（通过场景关联）
            if not await OntologyClassRepository(db).check_ownership_async(class_id, workspace_id):
                logger.warning(
                    f"Permission denied - class_id={class_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限操作该类型")

            # 执行删除
            success = await OntologyClassRepository(db).delete_async(class_id)

            logger.info(f"Class deleted successfully (async): {class_id}")

            return success

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to delete class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def get_class_by_id_async(
        db: AsyncSession,
        class_id: Any,
        workspace_id: Any
    ):
        """获取单个类型（异步版本）

        Args:
            db: 异步数据库会话
            class_id: 类型ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            Optional[OntologyClass]: 类型对象

        Raises:
            ValueError: 类型不存在或无权限
        """
        logger.debug(f"Getting class by ID (async): {class_id}")

        try:
            # 获取类型
            ontology_class = await OntologyClassRepository(db).get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Class not found: {class_id}")
                raise ValueError("类型不存在")

            # 检查权限（通过场景关联）
            if not await OntologyClassRepository(db).check_ownership_async(class_id, workspace_id):
                logger.warning(
                    f"Permission denied - class_id={class_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限访问该类型")

            return ontology_class

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to get class: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def get_class_by_name_async(
        db: AsyncSession,
        class_name: str,
        scene_id: Any,
        workspace_id: Any
    ):
        """根据类型名称获取类型（精确匹配，异步版本）

        Args:
            db: 异步数据库会话
            class_name: 类型名称
            scene_id: 场景ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            Optional[OntologyClass]: 类型对象

        Raises:
            ValueError: 类型不存在或无权限
        """
        logger.debug(f"Getting class by name (async): {class_name}, scene_id: {scene_id}")

        try:
            # 检查场景是否存在且属于当前工作空间
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限访问该场景")

            # 获取类型
            ontology_class = await OntologyClassRepository(db).get_by_name_async(class_name, scene_id)
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

    @staticmethod
    async def search_classes_by_name_async(
        db: AsyncSession,
        keyword: str,
        scene_id: Any,
        workspace_id: Any
    ) -> List:
        """根据关键词模糊搜索类型（异步版本）

        Args:
            db: 异步数据库会话
            keyword: 搜索关键词
            scene_id: 场景ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            List[OntologyClass]: 匹配的类型列表

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 搜索失败
        """
        logger.debug(
            f"Searching classes by keyword (async): {keyword}, "
            f"scene_id: {scene_id}, workspace_id: {workspace_id}"
        )

        try:
            # 检查场景是否存在且属于当前工作空间
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限访问该场景")

            # 搜索类型
            classes = await OntologyClassRepository(db).search_by_name_async(keyword, scene_id)

            logger.info(
                f"Found {len(classes)} classes matching keyword '{keyword}' "
                f"in scene {scene_id}"
            )

            return classes

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to search classes by keyword: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def list_classes_by_scene_async(
        db: AsyncSession,
        scene_id: Any,
        workspace_id: Any
    ) -> List:
        """获取场景下的所有类型（异步版本）

        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            List[OntologyClass]: 类型列表

        Raises:
            ValueError: 场景不存在或无权限
            RuntimeError: 查询失败
        """
        logger.debug(f"Listing classes (async) for scene: {scene_id}")

        try:
            # 检查场景是否存在且属于当前工作空间
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
                logger.warning(
                    f"Permission denied - scene_id={scene_id}, "
                    f"workspace_id={workspace_id}"
                )
                raise ValueError("无权限访问该场景的类型")

            # 获取类型列表
            class_repo = OntologyClassRepository(db)
            classes = await class_repo.get_classes_by_scene_async(scene_id)

            logger.info(f"Found {len(classes)} classes in scene {scene_id}")

            return classes

        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Failed to list classes: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    @staticmethod
    async def extract_ontology_async(
        db: AsyncSession,
        llm_client,
        scenario: str,
        domain: Optional[str] = None,
        scene_id: Optional[Any] = None,
        workspace_id: Optional[Any] = None,
        language: str = "zh"
    ) -> OntologyExtractionResponse:
        """执行本体提取（异步版本，全链路 async）

        验证输入 → 校验场景权限（async DB） → LLM 提取。

        Args:
            db: 异步数据库会话
            llm_client: LLM 客户端实例
            scenario: 场景描述文本
            domain: 可选的领域提示
            scene_id: 场景ID，用于权限验证
            workspace_id: 工作空间ID，用于权限验证
            language: 输出语言 ("zh" 中文, "en" 英文)

        Returns:
            OntologyExtractionResponse: 提取结果

        Raises:
            ValueError: 场景描述为空、场景不存在或无权限
            RuntimeError: 提取过程失败
        """
        start_time = time.time()

        if not scenario or not scenario.strip():
            logger.error("Scenario description is empty")
            raise ValueError("Scenario description cannot be empty")

        # 如果提供了scene_id,验证场景是否存在且有权限（async DB 调用）
        if scene_id and workspace_id:
            logger.info(f"Validating scene access - scene_id={scene_id}, workspace_id={workspace_id}")
            scene = await OntologySceneRepository(db).get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Scene not found: {scene_id}")
                raise ValueError("场景不存在")

            if not await OntologySceneRepository(db).check_ownership_async(scene_id, workspace_id):
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
            logger.info("Calling OntologyExtractor with default config")
            extraction_start_time = time.time()

            extractor = OntologyExtractor(llm_client)
            response = await extractor.extract_ontology_classes(
                scenario=scenario,
                domain=domain,
                max_classes=OntologyService.DEFAULT_MAX_CLASSES,
                min_classes=OntologyService.DEFAULT_MIN_CLASSES,
                enable_owl_validation=OntologyService.DEFAULT_ENABLE_OWL_VALIDATION,
                llm_temperature=OntologyService.DEFAULT_LLM_TEMPERATURE,
                llm_max_tokens=OntologyService.DEFAULT_LLM_MAX_TOKENS,
                max_description_length=OntologyService.DEFAULT_MAX_DESCRIPTION_LENGTH,
                timeout=OntologyService.DEFAULT_LLM_TIMEOUT,
                language=language,
            )

            extraction_duration = time.time() - extraction_start_time

            if not response.classes:
                logger.error("Ontology extraction failed: No classes extracted")
                raise RuntimeError("本体提取失败：结构化输出失败，未能提取到任何本体类")

            logger.info(
                "Extraction completed. Classes will be saved to ontology_class "
                "via /class endpoint based on user selection"
            )

            total_duration = time.time() - start_time

            logger.info(
                f"Ontology extraction service completed - "
                f"extracted_classes={len(response.classes)}, "
                f"domain={response.domain}, "
                f"extraction_duration={extraction_duration:.2f}s, "
                f"total_duration={total_duration:.2f}s"
            )

            return response

        except ValueError:
            total_duration = time.time() - start_time
            logger.error(f"Validation error after {total_duration:.2f}s", exc_info=True)
            raise
        except Exception as e:
            total_duration = time.time() - start_time
            error_msg = f"Ontology extraction failed after {total_duration:.2f}s: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
