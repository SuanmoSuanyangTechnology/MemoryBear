"""
Memory Reflection Controller

This module provides REST API endpoints for managing memory reflection configurations
and operations. It handles reflection engine setup, configuration management, and
execution of self-reflection processes across memory systems.

Key Features:
- Reflection configuration management (save, retrieve, update)
- Workspace-wide reflection execution across multiple applications
- Individual configuration-based reflection runs
- Multi-language support for reflection outputs
- Integration with Neo4j memory storage and LLM models
- Comprehensive error handling and logging
"""

import asyncio
import time
import uuid
from uuid import UUID

from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.reflection_engine.self_reflexion import (
    ReflectionConfig,
    ReflectionEngine, ReflectionRange, ReflectionBaseline,
)
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_reflection_schemas import Memory_Reflection
from app.services.memory_reflection_service import (
    MemoryReflectionService,
    WorkspaceAppService,
)
from app.services.model_service import ModelConfigService
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status,Header
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.config_utils import resolve_config_id

# Load environment variables for configuration
load_dotenv()

# Initialize API logger for request tracking and debugging
api_logger = get_api_logger()

# Configure router with prefix and tags for API organization
router = APIRouter(
    prefix="/memory",
    tags=["Memory"],
)


@router.post("/reflection/save")
async def save_reflection_config(
    request: Memory_Reflection,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Save reflection configuration to memory config table
    
    Persists reflection engine configuration settings to the data_config table,
    including reflection parameters, model settings, and evaluation criteria.
    Validates configuration parameters and ensures data consistency.
    
    Args:
        request: Memory reflection configuration data including:
            - config_id: Configuration identifier to update
            - reflection_enabled: Whether reflection is enabled
            - reflection_period_in_hours: Reflection execution interval
            - reflexion_range: Scope of reflection (partial/all)
            - baseline: Reflection strategy (time/fact/hybrid)
            - reflection_model_id: LLM model for reflection operations
            - memory_verify: Enable memory verification checks
            - quality_assessment: Enable quality assessment evaluation
        current_user: Authenticated user saving the configuration
        db: Database session for data operations
    
    Returns:
        dict: Success response with saved reflection configuration data
        
    Raises:
        HTTPException 400: If config_id is missing or parameters are invalid
        HTTPException 500: If configuration save operation fails
        
    Database Operations:
        - Updates memory_config table with reflection settings
        - Commits transaction and refreshes entity
        - Maintains configuration consistency
    """
    try:
        config_id = request.config_id
        config_id = resolve_config_id(config_id, db)
        if not config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少必需参数: config_id"
            )
        api_logger.info(f"用户 {current_user.username} 保存反思配置，config_id: {config_id}")

        # Update reflection configuration in database
        memory_config = MemoryConfigRepository.update_reflection_config(
            db,
            config_id=config_id,
            enable_self_reflexion=request.reflection_enabled,
            iteration_period=request.reflection_period_in_hours,
            reflexion_range=request.reflexion_range,
            baseline=request.baseline,
            reflection_model_id=request.reflection_model_id,
            memory_verify=request.memory_verify,
            quality_assessment=request.quality_assessment
        )

        # Commit transaction and refresh entity
        db.commit()
        db.refresh(memory_config)

        reflection_result={
                "config_id": memory_config.config_id,
                "enable_self_reflexion": memory_config.enable_self_reflexion,
                "iteration_period": memory_config.iteration_period,
                "reflexion_range": memory_config.reflexion_range,
                "baseline": memory_config.baseline,
                "reflection_model_id": memory_config.reflection_model_id,
                "memory_verify": memory_config.memory_verify,
                "quality_assessment": memory_config.quality_assessment}

        return success(data=reflection_result, msg="反思配置成功")
        

        
    except ValueError as ve:
        api_logger.error(f"参数错误: {str(ve)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"参数错误: {str(ve)}"
        )
    except Exception as e:
        api_logger.error(f"反思配置保存失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"反思配置保存失败: {str(e)}"
        )


@router.get("/reflection")
async def start_workspace_reflection(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Start reflection functionality for all matching applications in workspace
    
    Initiates reflection processes across all applications within the user's current
    workspace that have valid memory configurations. Processes each application's
    configurations and associated end users, executing reflection operations
    with proper error isolation and transaction management.
    
    This endpoint serves as a workspace-wide reflection orchestrator, ensuring
    that reflection failures for individual users don't affect other operations.
    
    Args:
        current_user: Authenticated user initiating workspace reflection
        db: Database session for configuration queries
    
    Returns:
        dict: Success response with reflection results for all processed applications:
            - app_id: Application identifier
            - config_id: Memory configuration identifier
            - end_user_id: End user identifier
            - reflection_result: Individual reflection operation result
    
    Processing Logic:
        1. Retrieve all applications in the current workspace
        2. Filter applications with valid memory configurations
        3. For each configuration, find matching releases
        4. Execute reflection for each end user with isolated transactions
        5. Aggregate results with error handling per user
    
    Error Handling:
        - Individual user reflection failures are isolated
        - Failed operations are logged and included in results
        - Database transactions are isolated per user to prevent cascading failures
        - Comprehensive error reporting for debugging
    
    Raises:
        HTTPException 500: If workspace reflection initialization fails
        
    Performance Notes:
        - Uses independent database sessions for each user operation
        - Prevents transaction failures from affecting other users
        - Comprehensive logging for operation tracking
    """
    workspace_id = current_user.current_workspace_id

    try:
        api_logger.info(f"用户 {current_user.username} 启动workspace反思，workspace_id: {workspace_id}")

        # Use independent database session to get workspace app details, avoiding transaction failures
        from app.db import get_db_context
        with get_db_context() as query_db:
            service = WorkspaceAppService(query_db)
            result = service.get_workspace_apps_detailed(workspace_id)
        
        reflection_results = []
        
        # Process each application in the workspace
        for data in result['apps_detailed_info']:
            # Skip applications without configurations
            if not data['memory_configs']:
                api_logger.debug(f"应用 {data['id']} 没有memory_configs，跳过")
                continue

            releases = data['releases']
            memory_configs = data['memory_configs']
            end_users = data['end_users']

            # Execute reflection for each configuration and user combination
            for config in memory_configs:
                config_id_str = str(config['config_id'])

                # Find all releases matching this configuration
                matching_releases = [r for r in releases if str(r['config']) == config_id_str]

                if not matching_releases:
                    api_logger.debug(f"配置 {config_id_str} 没有匹配的release")
                    continue

                # Execute reflection for each user - using independent database sessions
                for user in end_users:
                    api_logger.info(f"为用户 {user['id']} 启动反思，config_id: {config_id_str}")

                    # Create independent database session for each user to avoid transaction failure impact
                    with get_db_context() as user_db:
                        try:
                            reflection_service = MemoryReflectionService(user_db)
                            reflection_result = await reflection_service.start_text_reflection(
                                config_data=config,
                                end_user_id=user['id']
                            )

                            reflection_results.append({
                                "app_id": data['id'],
                                "config_id": config_id_str,
                                "end_user_id": user['id'],
                                "reflection_result": reflection_result
                            })
                        except Exception as e:
                            api_logger.error(f"用户 {user['id']} 反思失败: {str(e)}")
                            reflection_results.append({
                                "app_id": data['id'],
                                "config_id": config_id_str,
                                "end_user_id": user['id'],
                                "reflection_result": {
                                    "status": "错误",
                                    "message": f"反思失败: {str(e)}"
                                }
                            })

        return success(data=reflection_results, msg="反思配置成功")

    except Exception as e:
        api_logger.error(f"启动workspace反思失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"启动workspace反思失败: {str(e)}"
        )


@router.get("/reflection/configs")
async def start_reflection_configs(
        config_id: uuid.UUID|int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """
    Query reflection configuration information by config_id
    
    Retrieves detailed reflection configuration settings from the memory_config
    table for a specific configuration ID. Provides comprehensive reflection
    parameters including model settings, evaluation criteria, and operational flags.
    
    Args:
        config_id: Configuration identifier (UUID or integer) to query
        current_user: Authenticated user making the request
        db: Database session for data operations
    
    Returns:
        dict: Success response with detailed reflection configuration:
            - config_id: Resolved configuration identifier
            - reflection_enabled: Whether reflection is enabled for this config
            - reflection_period_in_hours: Reflection execution interval
            - reflexion_range: Scope of reflection operations (partial/all)
            - baseline: Reflection strategy (time/fact/hybrid)
            - reflection_model_id: LLM model identifier for reflection
            - memory_verify: Memory verification flag
            - quality_assessment: Quality assessment flag
    
    Database Operations:
        - Queries memory_config table by resolved config_id
        - Retrieves all reflection-related configuration fields
        - Resolves configuration ID for consistent formatting
    
    Raises:
        HTTPException 404: If configuration with specified ID is not found
        HTTPException 500: If configuration query operation fails
        
    ID Resolution:
        - Supports both UUID and integer config_id formats
        - Automatically resolves to appropriate internal format
        - Maintains consistency across different ID representations
    """
    config_id = resolve_config_id(config_id, db)
    try:
        config_id=resolve_config_id(config_id,db)
        api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {config_id}")
        result = MemoryConfigRepository.query_reflection_config_by_id(db, config_id)
        memory_config_id = resolve_config_id(result.config_id, db)
        
        # Build response data with comprehensive configuration details
        reflection_config = {
            "config_id": memory_config_id,
            "reflection_enabled": result.enable_self_reflexion,
            "reflection_period_in_hours": result.iteration_period,
            "reflexion_range": result.reflexion_range,
            "baseline": result.baseline,
            "reflection_model_id": result.reflection_model_id,
            "memory_verify": result.memory_verify,
            "quality_assessment": result.quality_assessment
        }
        api_logger.info(f"成功查询反思配置，config_id: {config_id}")
        return success(data=reflection_config, msg="反思配置查询成功")

        api_logger.info(f"Successfully queried reflection config, config_id: {config_id}")
        return success(data=reflection_config, msg="Reflection configuration query successful")
        
    except HTTPException:
        # Re-raise HTTP exceptions without modification
        raise
    except Exception as e:
        api_logger.error(f"查询反思配置失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询反思配置失败: {str(e)}"
        )

@router.get("/reflection/run")
async def reflection_run(
    config_id: UUID|int,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Execute reflection engine with specified configuration
    
    Runs the reflection engine using configuration parameters from the database.
    Validates model availability, sets up the reflection engine with proper
    configuration, and executes the reflection process with multi-language support.
    
    This endpoint provides a test run capability for reflection configurations,
    allowing users to validate their reflection settings and see results before
    deploying to production environments.
    
    Args:
        config_id: Configuration identifier (UUID or integer) for reflection settings
        language_type: Language preference header for output localization (optional)
        current_user: Authenticated user executing the reflection
        db: Database session for configuration queries
    
    Returns:
        dict: Success response with reflection execution results including:
            - baseline: Reflection strategy used
            - source_data: Input data processed
            - memory_verifies: Memory verification results (if enabled)
            - quality_assessments: Quality assessment results (if enabled)
            - reflexion_data: Generated reflection insights and solutions
    
    Configuration Validation:
        - Verifies configuration exists in database
        - Validates LLM model availability
        - Falls back to default model if specified model is unavailable
        - Ensures all required parameters are properly set
    
    Reflection Engine Setup:
        - Creates ReflectionConfig with database parameters
        - Initializes Neo4j connector for memory access
        - Sets up ReflectionEngine with validated model
        - Configures language preferences for output
    
    Error Handling:
        - Model validation with fallback to default
        - Configuration validation and error reporting
        - Comprehensive logging for debugging
        - Graceful handling of missing configurations
    
    Raises:
        HTTPException 404: If configuration is not found
        HTTPException 500: If reflection execution fails
        
    Performance Notes:
        - Direct database query for configuration retrieval
        - Model validation to prevent runtime failures
        - Efficient reflection engine initialization
        - Language-aware output processing
    """
    # Use centralized language validation for consistent localization
    language = get_language_from_header(language_type)

    api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {config_id}")
    config_id = resolve_config_id(config_id, db)
    
    # Query reflection configuration using MemoryConfigRepository
    result = MemoryConfigRepository.query_reflection_config_by_id(db, config_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到config_id为 {config_id} 的配置"
        )

    api_logger.info(f"成功查询反思配置，config_id: {config_id}")

    # Validate model ID existence
    model_id = result.reflection_model_id
    if model_id:
        try:
            ModelConfigService.get_model_by_id(db=db, model_id=uuid.UUID(model_id))
            api_logger.info(f"模型ID验证成功: {model_id}")
        except Exception as e:
            api_logger.warning(f"模型ID '{model_id}' 不存在，将使用默认模型: {str(e)}")
            # 可以设置为None，让反思引擎使用默认模型
            model_id = None

    # Create reflection configuration with database parameters
    config = ReflectionConfig(
        enabled=result.enable_self_reflexion,
        iteration_period=result.iteration_period,
        reflexion_range=ReflectionRange(result.reflexion_range),
        baseline=ReflectionBaseline(result.baseline),
        output_example='',
        memory_verify=result.memory_verify,
        quality_assessment=result.quality_assessment,
        violation_handling_strategy="block",
        model_id=model_id,
        language_type=language_type
    )
    
    # Initialize Neo4j connector and reflection engine
    connector = Neo4jConnector()
    engine = ReflectionEngine(
        config=config,
        neo4j_connector=connector,
        llm_client=model_id  # Pass validated model_id
    )

    result=await (engine.reflection_run())
    return success(data=result, msg="反思试运行")




