"""
Self-Reflection Engine Implementation

This module implements the self-reflection functionality of the memory system, including:
1. Time-based reflection - Triggers reflection based on time cycles
2. Fact-based reflection - Detects and resolves memory conflicts
3. Comprehensive reflection - Integrates multiple reflection strategies
4. Reflection result application - Updates memory database
"""

import asyncio
import json
import logging
import os
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.config.get_data import (
    extract_and_process_changes,
    get_data,
    get_data_statement,
)
from app.core.models.base import RedBearModelConfig
from app.repositories.neo4j.cypher_queries import (
    neo4j_query_all,
    neo4j_query_part,
    neo4j_statement_all,
    neo4j_statement_part,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.neo4j_update import neo4j_data
from app.schemas.memory_storage_schema import (
    ConflictResultSchema,
    ReflexionResultSchema,
)
from pydantic import BaseModel

# Configure logging
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
else:
    _root_logger.setLevel(logging.INFO)

class TranslationResponse(BaseModel):
    """Translation response model for language conversion"""
    data: str
    
class ReflectionRange(str, Enum):
    """
    Reflection range enumeration
    
    Defines the scope of data to be included in reflection operations.
    """
    PARTIAL = "partial"  # Reflect from retrieval results
    ALL = "all"  # Reflect from entire database


class ReflectionBaseline(str, Enum):
    """
    Reflection baseline enumeration
    
    Defines the strategy or approach used for reflection operations.
    """
    TIME = "TIME"  # Time-based reflection
    FACT = "FACT"  # Fact-based reflection
    HYBRID = "HYBRID"  # Hybrid reflection combining multiple strategies


class ReflectionConfig(BaseModel):
    """
    Reflection engine configuration
    
    Defines all configuration parameters for the reflection engine including
    operation modes, model settings, and evaluation criteria.
    
    Attributes:
        enabled: Whether reflection engine is enabled
        iteration_period: Reflection cycle period (e.g., "3" hours)
        reflexion_range: Scope of reflection (PARTIAL or ALL)
        baseline: Reflection strategy (TIME, FACT, or HYBRID)
        model_id: LLM model identifier for reflection operations
        end_user_id: User identifier for scoped operations
        output_example: Example output format for guidance
        memory_verify: Enable memory verification checks
        quality_assessment: Enable quality assessment evaluation
        violation_handling_strategy: Strategy for handling violations
        language_type: Language type for output ("zh" or "en")
    """
    enabled: bool = False
    iteration_period: str = "3"  # Reflection cycle period
    reflexion_range: ReflectionRange = ReflectionRange.PARTIAL
    baseline: ReflectionBaseline = ReflectionBaseline.TIME
    model_id: Optional[str] = None  # Model ID
    end_user_id: Optional[str] = None
    output_example: Optional[str] = None  # Output example

    # Evaluation related fields
    memory_verify: bool = True  # Memory verification
    quality_assessment: bool = True  # Quality assessment
    violation_handling_strategy: str = "warn"  # Violation handling strategy
    language_type: str = "zh"

    class Config:
        use_enum_values = True


class ReflectionResult(BaseModel):
    """
    Reflection operation result
    
    Contains comprehensive information about the outcome of a reflection operation
    including success status, metrics, and execution details.
    
    Attributes:
        success: Whether the reflection operation succeeded
        message: Descriptive message about the operation result
        conflicts_found: Number of conflicts detected during reflection
        conflicts_resolved: Number of conflicts successfully resolved
        memories_updated: Number of memory entries updated in database
        execution_time: Total time taken for the reflection operation
        details: Additional details about the operation (optional)
    """
    success: bool
    message: str
    conflicts_found: int = 0
    conflicts_resolved: int = 0
    memories_updated: int = 0
    execution_time: float = 0.0
    details: Optional[Dict[str, Any]] = None


class ReflectionEngine:
    """
    Self-Reflection Engine
    
    Responsible for executing memory system self-reflection operations including
    conflict detection, conflict resolution, and memory updates. Supports multiple
    reflection strategies and provides comprehensive result tracking.
    
    The engine can operate in different modes:
    - Time-based: Reflects on memories within specific time periods
    - Fact-based: Detects and resolves factual conflicts in memories
    - Hybrid: Combines multiple reflection strategies
    
    Attributes:
        config: Reflection engine configuration
        neo4j_connector: Neo4j database connector
        llm_client: Language model client for analysis
        Various function handlers for data processing and prompt rendering
    """

    def __init__(
            self,
            config: ReflectionConfig,
            neo4j_connector: Optional[Any] = None,
            llm_client: Optional[Any] = None,
            get_data_func: Optional[Any] = None,
            render_evaluate_prompt_func: Optional[Any] = None,
            render_reflexion_prompt_func: Optional[Any] = None,
            conflict_schema: Optional[Any] = None,
            reflexion_schema: Optional[Any] = None,
            update_query: Optional[str] = None
    ):
        """
        Initialize reflection engine
        
        Sets up the reflection engine with configuration and optional dependencies.
        Uses lazy initialization to avoid circular imports and optimize startup time.

        Args:
            config: Reflection engine configuration object
            neo4j_connector: Neo4j connector instance (optional, will be created if not provided)
            llm_client: LLM client instance (optional, will be created if not provided)
            get_data_func: Function for retrieving data (optional, uses default if not provided)
            render_evaluate_prompt_func: Function for rendering evaluation prompts (optional)
            render_reflexion_prompt_func: Function for rendering reflection prompts (optional)
            conflict_schema: Schema for conflict result validation (optional)
            reflexion_schema: Schema for reflection result validation (optional)
            update_query: Query string for database updates (optional)
        """
        self.config = config
        self.neo4j_connector = neo4j_connector
        self.llm_client = llm_client
        self.get_data_func = get_data_func
        self.render_evaluate_prompt_func = render_evaluate_prompt_func
        self.render_reflexion_prompt_func = render_reflexion_prompt_func
        self.conflict_schema = conflict_schema
        self.reflexion_schema = reflexion_schema
        self.update_query = update_query
        self._semaphore = asyncio.Semaphore(5)  # Default concurrency limit of 5


        # Lazy import to avoid circular dependencies
        self._lazy_init_done = False

    def _lazy_init(self):
        """
        Lazy initialization to avoid circular imports
        
        Initializes dependencies only when needed, preventing circular import issues
        and optimizing startup performance. Sets up default implementations for
        any components not provided during construction.
        """
        if self._lazy_init_done:
            return

        if self.neo4j_connector is None:
            self.neo4j_connector = Neo4jConnector()

        if self.llm_client is None:
            from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
            from app.db import get_db_context
            with get_db_context() as db:
                factory = MemoryClientFactory(db)
                self.llm_client = factory.get_llm_client(self.config.model_id)
        elif isinstance(self.llm_client, str):
            # If llm_client is a string (model_id), use it to initialize the client
            from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
            from app.db import get_db_context
            from app.services.memory_config_service import MemoryConfigService
            model_id = self.llm_client
            with get_db_context() as db:
                factory = MemoryClientFactory(db)
                # self.llm_client = factory.get_llm_client(model_id)
                
                # Use MemoryConfigService to get model config
                config_service = MemoryConfigService(db)
                model_config = config_service.get_model_config(model_id)
                
            extra_params={
                    "temperature": 0.2,  # Lower temperature for faster response and consistency
                    "max_tokens": 600,  # Limit maximum token count
                    "top_p": 0.8,  # Optimize sampling parameters
                    "stream": False,  # Ensure non-streaming output for fastest response
                }

            self.llm_client  = OpenAIClient(RedBearModelConfig(
                model_name=model_config.get("model_name"),
                provider=model_config.get("provider"),
                api_key=model_config.get("api_key"),
                base_url=model_config.get("base_url"),
                timeout=model_config.get("timeout", 30),
                max_retries=model_config.get("max_retries", 2),
                extra_params=extra_params
            ), type_=model_config.get("type"))

        if self.get_data_func is None:
            self.get_data_func = get_data

        # Import get_data_statement function
        if not hasattr(self, 'get_data_statement'):
            self.get_data_statement = get_data_statement

        if self.render_evaluate_prompt_func is None:
            from app.core.memory.utils.prompt.template_render import (
                render_evaluate_prompt,
            )
            self.render_evaluate_prompt_func = render_evaluate_prompt

        if self.render_reflexion_prompt_func is None:
            from app.core.memory.utils.prompt.template_render import (
                render_reflexion_prompt,
            )
            self.render_reflexion_prompt_func = render_reflexion_prompt

        if self.conflict_schema is None:
            self.conflict_schema = ConflictResultSchema

        if self.reflexion_schema is None:
            self.reflexion_schema = ReflexionResultSchema

        if self.update_query is None:
            from app.repositories.neo4j.cypher_queries import (
                UPDATE_STATEMENT_INVALID_AT,
            )
            self.update_query = UPDATE_STATEMENT_INVALID_AT

        self._lazy_init_done = True

    async def execute_reflection(self, host_id) -> ReflectionResult:
        """
        Execute complete reflection workflow
        
        Performs the full reflection process including data retrieval, conflict detection,
        conflict resolution, and memory updates. This is the main entry point for
        reflection operations.
        
        Args:
            host_id: Host identifier for scoping reflection operations
            
        Returns:
            ReflectionResult: Comprehensive result of the reflection operation including
                            success status, conflict metrics, and execution time
        """
        # Lazy initialization
        self._lazy_init()

        if not self.config.enabled:
            return ReflectionResult(
                success=False,
                message="反思引擎未启用"
            )

        start_time = asyncio.get_event_loop().time()
        logging.info("====== 自我反思流程开始 ======")

        print(self.config.baseline, self.config.memory_verify, self.config.quality_assessment)
        try:
            # 1. Get reflection data
            reflexion_data, statement_databasets = await self._get_reflexion_data(host_id)
            if not reflexion_data:
                return ReflectionResult(
                    success=True,
                    message="无反思数据，结束反思",
                    execution_time=asyncio.get_event_loop().time() - start_time
                )

            # 2. Detect conflicts (fact-based reflection)
            conflict_data = await self._detect_conflicts(reflexion_data, statement_databasets)
            conflict_list=[]
            for i  in conflict_data:
                conflict_list.append(i['data'])



            conflicts_found=0
            # 3. Resolve conflicts
            solved_data = await self._resolve_conflicts(conflict_list, statement_databasets)

            if not solved_data:
                return ReflectionResult(
                    success=False,
                    message=f"没有{self.config.baseline}相关的冲突数据",
                    conflicts_found=conflicts_found,
                    execution_time=asyncio.get_event_loop().time() - start_time
                )

            conflicts_resolved = len(solved_data)
            logging.info(f"解决了 {conflicts_resolved} 个冲突")


            # 4. Apply reflection results (update memory database)
            memories_updated=await self._apply_reflection_results(solved_data)

            execution_time = asyncio.get_event_loop().time() - start_time

            logging.info("====== 自我反思流程结束 ======")

            return ReflectionResult(
                success=True,
                message="反思完成",
                conflicts_found=conflicts_found,
                conflicts_resolved=conflicts_resolved,
                memories_updated=memories_updated,
                execution_time=execution_time,

            )

        except Exception as e:
            logging.error(f"反思流程执行失败: {e}", exc_info=True)
            return ReflectionResult(
                success=False,
                message=f"反思流程执行失败: {str(e)}",
                execution_time=asyncio.get_event_loop().time() - start_time
            )

    async def Translate(self, text):
        """
        Translate Chinese text to English
        
        Uses the configured LLM to translate Chinese text to English with structured output.
        Provides consistent translation format for reflection results.
        
        Args:
            text: Chinese text to be translated
            
        Returns:
            str: Translated English text
        """
        # Translate Chinese to English
        translation_messages = [
            {
                "role": "user",
                "content": f"{text}\n\n中文翻译为英文，输出格式为{{\"data\":\"翻译后的内容\"}}"
            }
        ]

        response = await self.llm_client.response_structured(
            messages=translation_messages,
            response_model=TranslationResponse
        )
        return response.data
    async def extract_translation(self,data):
        """
        Extract and translate reflection data to English
        
        Processes reflection data structure and translates all Chinese content to English.
        Handles nested data structures including memory verifications, quality assessments,
        and reflection data while preserving the original structure.
        
        Args:
            data: Dictionary containing reflection data with Chinese content
            
        Returns:
            dict: Translated data structure with English content
        """
        end_datas={}
        end_datas['source_data']=await self.Translate(data['source_data'])
        quality_assessments = []
        memory_verifies = []
        reflexion_data=[]
        if data['memory_verifies']!=[]:
            for i in data['memory_verifies']:
                end_data={}
                end_data['has_privacy'] = i['has_privacy']
                privacy=i['privacy_types']
                privacy_types_=[]
                for pri in privacy:
                    privacy_types_.append(await self.Translate(pri))
                end_data['privacy_types']=privacy_types_
                end_data['summary']=await self.Translate(i['summary'])
                memory_verifies.append(end_data)
        end_datas['memory_verifies']=memory_verifies

        if data['quality_assessments']!=[]:
            for i in data['quality_assessments']:
                end_data = {}
                end_data['score']=i['score']
                end_data['summary'] = await self.Translate(i['summary'])
                quality_assessments.append(end_data)
        end_datas['quality_assessments'] = quality_assessments
        for i in data['reflexion_data']:
            end_data = {}
            end_data['reason'] = await self.Translate(i['reason'])
            end_data['solution'] = await self.Translate(i['solution'])
            reflexion_data.append(end_data)
        end_datas['reflexion_data'] = reflexion_data
        return end_datas

    async def reflection_run(self):
        """
        Execute reflection workflow with comprehensive data processing
        
        Performs a complete reflection operation including conflict detection, resolution,
        and result formatting. Supports both Chinese and English output based on
        configuration settings.
        
        Returns:
            dict: Comprehensive reflection results including source data, memory verifications,
                 quality assessments, and reflection data. Results are translated to English
                 if language_type is set to 'en'.
        """
        self._lazy_init()
        start_time = time.time()
        memory_verifies_flag = self.config.memory_verify
        quality_assessment=self.config.quality_assessment
        language_type=self.config.language_type

        asyncio.get_event_loop().time()
        logging.info("====== 自我反思流程开始 ======")

        result_data = {}

        source_data, databasets = await self.extract_fields_from_json()
        result_data['baseline'] = self.config.baseline

        result_data['source_data'] = "我是 2023 年春天去北京工作的，后来基本一直都在北京上班，也没怎么换过城市。不过后来公司调整，2024 年上半年我被调到上海待了差不多半年，那段时间每天都是在上海办公室打卡。当时入职资料用的还是我之前的身份信息，身份证号是 11010119950308123X，银行卡是 6222023847595898，这些一直没变。对了，其实我 从 2023 年开始就一直在北京生活，从来没有长期离开过北京，上海那段更多算是远程配合"
        # 2. 检测冲突（基于事实的反思）
        conflict_data = await self._detect_conflicts(databasets, source_data)
        # Traverse data to extract fields
        quality_assessments = []
        memory_verifies = []
        for item in conflict_data:
            quality_assessments.append(item['quality_assessment'])
            memory_verifies.append(item['memory_verify'])
        result_data['memory_verifies'] = memory_verifies
        result_data['quality_assessments'] = quality_assessments
        conflicts_found = 0  # Initialize as integer 0 instead of empty string
        REMOVE_KEYS = {"created_at", "expired_at","relationship","predicate","statement_id","id","statement_id","relationship_statement_id"}
        # Clean conflict_data, and memory_verify and quality_assessment
        cleaned_conflict_data = []
        for item in conflict_data:
            cleaned_item = {
                'data': item['data'],
                'conflict': item['conflict']
            }
            cleaned_conflict_data.append(cleaned_item)
        cleaned_conflict_data_=[]
        for item in conflict_data:
            cleaned_data = []
            for row in item.get("data", []):
                # Remove created_at / expired_at
                cleaned_row = {
                    k: v
                    for k, v in row.items()
                    if k not in REMOVE_KEYS
                }
                cleaned_data.append(cleaned_row)
            cleaned_item = {
                "data": cleaned_data,
                "conflict": item.get("conflict"),
            }
            cleaned_conflict_data_.append(cleaned_item)
        print(cleaned_conflict_data_)
        # 3. Resolve conflicts
        solved_data = await self._resolve_conflicts(cleaned_conflict_data_, source_data)
        if not solved_data:
            return ReflectionResult(
                success=False,
                message="反思失败，未解决冲突",
                conflicts_found=conflicts_found,
                execution_time=asyncio.get_event_loop().time() - start_time
            )
        reflexion_data = []

        # Traverse data to extract reflexion fields
        for item in solved_data:
            if 'results' in item:
                for result in item['results']:
                    reflexion_data.append(result['reflexion'])
        result_data['reflexion_data'] = reflexion_data
        if memory_verifies_flag==False:
            result_data['memory_verifies']=[]
        if quality_assessment==False:
            result_data['quality_assessments']=[]

        if language_type=='en':
            result_data=await self.extract_translation(result_data)
        print(time.time()-start_time,'----------')
        return result_data


    async def extract_fields_from_json(self):
        """
        Extract source_data and databasets fields from example.json
        
        Reads reflection example data from the example.json file and extracts
        the source data and database statements for testing and demonstration purposes.
        
        Returns:
            tuple: (source_data, databasets) extracted from the example file
                  Returns empty lists if file reading fails
        """

        prompt_dir = os.path.join(os.path.dirname(__file__), "example")
        try:
            # Read JSON file
            with open(prompt_dir + '/example.json', 'r', encoding='utf-8') as f:
                data = json.loads(f.read())

            # Extract fields under memory_verify
            memory_verify = data.get("memory_verify", {})
            source_data = memory_verify.get("source_data", [])
            databasets = memory_verify.get("databasets", [])

            return source_data, databasets

        except Exception as e:
            return [], []

    async def _get_reflexion_data(self, host_id: uuid.UUID) -> List[Any]:
        """
        Get reflection data from database
        
        Retrieves memory data for reflection based on the configured reflection range.
        Supports both partial (from retrieval results) and full (entire database) modes.

        Args:
            host_id: Host UUID identifier for scoping data retrieval

        Returns:
            tuple: (reflexion_data, statement_data) containing memory data for reflection
                  Returns empty lists if query fails
        """

        print("=== 获取反思数据 ===")
        print(f"  主机ID: {host_id}")
        if self.config.reflexion_range == ReflectionRange.PARTIAL:
            neo4j_query = neo4j_query_part.format(host_id)
            neo4j_statement = neo4j_statement_part.format(host_id)
        elif self.config.reflexion_range == ReflectionRange.ALL:
            neo4j_query = neo4j_query_all.format(host_id)
            neo4j_statement = neo4j_statement_all.format(host_id)
        try:
            result = await self.neo4j_connector.execute_query(neo4j_query)
            result_statement = await self.neo4j_connector.execute_query(neo4j_statement)
            neo4j_databasets = await  self.get_data_func(result)
            neo4j_state = await  self.get_data_statement(result_statement)
            return neo4j_databasets, neo4j_state


        except Exception as e:
            logging.error(f"Neo4j查询失败: {e}")
            return [], []

    async def _detect_conflicts(self, data: List[Any], statement_databasets: List[Any]) -> List[Any]:
        """
        Detect conflicts (fact-based reflection)
        
        Uses LLM to analyze memory data and detect conflicts within the memories.
        Performs comprehensive conflict detection including memory verification and
        quality assessment based on configuration settings.

        Args:
            data: Memory data to be analyzed for conflicts
            statement_databasets: Statement database records for context

        Returns:
            List[Any]: List of detected conflicts with detailed analysis
        """
        if not data:
            return []

        # Data preprocessing: if data is too small, return no conflicts directly
        if len(data) < 2:
            logging.info("数据量不足，无需检测冲突")
            return []

        # Use converted data
        # print("Converted data:", data[:2] if len(data) > 2 else data)  # Only print first 2 to avoid long logs
        memory_verify = self.config.memory_verify

        logging.info("====== 冲突检测开始 ======")
        start_time = asyncio.get_event_loop().time()
        quality_assessment = self.config.quality_assessment
        language_type=self.config.language_type

        try:
            # Render conflict detection prompt
            rendered_prompt = await self.render_evaluate_prompt_func(
                data,
                self.conflict_schema,
                self.config.baseline,
                memory_verify,
                quality_assessment,
                statement_databasets,
                language_type
            )

            messages = [{"role": "user", "content": rendered_prompt}]
            logging.info(f"提示词长度: {len(rendered_prompt)}")

            # Call LLM for conflict detection
            response = await self.llm_client.response_structured(
                messages,
                self.conflict_schema
            )

            execution_time = asyncio.get_event_loop().time() - start_time
            logging.info(f"冲突检测耗时: {execution_time:.2f} 秒")

            if not response:
                logging.error("LLM 冲突检测输出解析失败")
                return []

            # Standardize return format
            if isinstance(response, BaseModel):
                return [response.model_dump()]
            elif hasattr(response, 'dict'):
                return [response.dict()]
            else:
                return [response]

        except Exception as e:
            logging.error(f"冲突检测失败: {e}", exc_info=True)
            return []

    async def _resolve_conflicts(self, conflicts: List[Any], statement_databasets: List[Any]) -> List[Any]:
        """
        Resolve detected conflicts
        
        Uses LLM to perform reflection and resolution on detected conflicts.
        Processes conflicts in parallel for efficiency while respecting concurrency limits.

        Args:
            conflicts: List of conflicts to be resolved
            statement_databasets: Statement database records for context

        Returns:
            List[Any]: List of resolution solutions with reflection results
        """
        if not conflicts:
            return []

        logging.info("====== 冲突解决开始 ======")
        baseline = self.config.baseline
        memory_verify = self.config.memory_verify

        # Process each conflict in parallel
        async def _resolve_one(conflict: Any) -> Optional[Dict[str, Any]]:
            """Resolve a single conflict"""
            async with self._semaphore:
                try:
                    # Render reflection prompt
                    rendered_prompt = await self.render_reflexion_prompt_func(
                        [conflict],
                        self.reflexion_schema,
                        baseline,
                        memory_verify,
                        statement_databasets
                    )
                    logging.info(f"提示词长度: {len(rendered_prompt)}")

                    messages = [{"role": "user", "content": rendered_prompt}]

                    # Call LLM for reflection
                    response = await self.llm_client.response_structured(
                        messages,
                        self.reflexion_schema
                    )

                    if not response:
                        return None

                    # Standardize return format
                    if isinstance(response, BaseModel):
                        return response.model_dump()
                    elif hasattr(response, 'dict'):
                        return response.dict()
                    elif isinstance(response, dict):
                        return response
                    else:
                        return None

                except Exception as e:
                    logging.warning(f"解决单个冲突失败: {e}")
                    return None

        # Execute all conflict resolution tasks concurrently
        tasks = [_resolve_one(conflict) for conflict in conflicts]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Filter out failed results
        solved = [r for r in results if r is not None]

        logging.info(f"成功解决 {len(solved)}/{len(conflicts)} 个冲突")

        return solved

    async def _apply_reflection_results(
            self,
            solved_data: List[Dict[str, Any]]
    ) -> int:
        """
        Apply reflection results (update memory database)
        
        Updates the Neo4j database with resolved conflicts and reflection results.
        Processes the solved data and applies changes to the memory storage system.

        Args:
            solved_data: List of resolved conflict solutions with reflection data

        Returns:
            int: Number of successfully updated memory entries
        """
        changes = extract_and_process_changes(solved_data)
        success_count = await neo4j_data(changes)
        return success_count



    # Time-based reflection methods
    async def time_based_reflection(
            self,
            host_id: uuid.UUID,
            time_period: Optional[str] = None
    ) -> ReflectionResult:
        """
        Time-based reflection
        
        Triggers reflection based on time cycles, checking memories within
        specified time periods. Uses the configured iteration period if
        no specific time period is provided.

        Args:
            host_id: Host UUID identifier for scoping reflection
            time_period: Time period (e.g., "three hours"), uses config value if not provided

        Returns:
            ReflectionResult: Comprehensive reflection operation result
        """
        period = time_period or self.config.iteration_period
        logging.info(f"执行基于时间的反思，周期: {period}")

        # Use standard reflection workflow
        return await self.execute_reflection(host_id)

    # Fact-based reflection methods
    async def fact_based_reflection(
            self,
            host_id: uuid.UUID
    ) -> ReflectionResult:
        """
        Fact-based reflection
        
        Detects and resolves factual conflicts within memories. Analyzes
        memory data for inconsistencies and contradictions that need resolution.

        Args:
            host_id: Host UUID identifier for scoping reflection

        Returns:
            ReflectionResult: Comprehensive reflection operation result
        """
        logging.info("执行基于事实的反思")

        # Use standard reflection workflow
        return await self.execute_reflection(host_id)

    # Comprehensive reflection methods
    async def comprehensive_reflection(
            self,
            host_id: uuid.UUID
    ) -> ReflectionResult:
        """
        Comprehensive reflection
        
        Integrates time-based and fact-based reflection strategies based on
        the configured baseline. Supports hybrid approaches that combine
        multiple reflection methodologies.

        Args:
            host_id: Host UUID identifier for scoping reflection

        Returns:
            ReflectionResult: Comprehensive reflection operation result combining
                            multiple strategies if using hybrid baseline
        """
        logging.info("执行综合反思")

        # Choose reflection strategy based on configured baseline
        if self.config.baseline == ReflectionBaseline.TIME:
            return await self.time_based_reflection(host_id)
        elif self.config.baseline == ReflectionBaseline.FACT:
            return await self.fact_based_reflection(host_id)
        elif self.config.baseline == ReflectionBaseline.HYBRID:
            # Hybrid strategy: execute time-based reflection first, then fact-based reflection
            time_result = await self.time_based_reflection(host_id)
            fact_result = await self.fact_based_reflection(host_id)

            # Merge results
            return ReflectionResult(
                success=time_result.success and fact_result.success,
                message=f"时间反思: {time_result.message}; 事实反思: {fact_result.message}",
                conflicts_found=time_result.conflicts_found + fact_result.conflicts_found,
                conflicts_resolved=time_result.conflicts_resolved + fact_result.conflicts_resolved,
                memories_updated=time_result.memories_updated + fact_result.memories_updated,
                execution_time=time_result.execution_time + fact_result.execution_time
            )
        else:

            raise ValueError(f"未知的反思基线: {self.config.baseline}")

