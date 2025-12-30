import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.memory.models.message_models import DialogData, Statement

#避免在测试收集阶段因为 OpenAIClient 间接引入 langfuse 导致 ModuleNotFoundError 。这只是类型注解与导入时机的调整，不改变实现。
from app.core.memory.models.variate_config import StatementExtractionConfig
from app.core.memory.utils.data.ontology import (
    LABEL_DEFINITIONS,
    RelevenceInfo,
    StatementType,
    TemporalInfo,
)
from app.core.memory.utils.prompt.prompt_utils import render_statement_extraction_prompt
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

class ExtractedStatement(BaseModel):
    """Schema for extracted statement from LLM"""
    statement: str = Field(..., description="The extracted statement text")
    statement_type: str = Field(..., description="FACT, OPINION,SUGGESTION or PREDICTION")
    temporal_type: str = Field(..., description="STATIC, DYNAMIC, ATEMPORAL")
    relevence: str = Field(..., description="RELEVANT or IRRELEVANT")

# 统一使用 StatementExtractionResponse 作为 LLM 的结构化返回（仅语句）
class StatementExtractionResponse(BaseModel):
    statements: List[ExtractedStatement] = Field(default_factory=list, description="List of extracted statements")
    
    @field_validator('statements', mode='before')
    @classmethod
    def filter_empty_statements(cls, v):
        """Filter out empty or invalid statement dicts before validation.
        
        This handles cases where the LLM returns malformed responses with empty dicts,
        which can happen due to response truncation or parsing issues (especially with
        providers like Bedrock that don't support with_structured_output).
        """
        if isinstance(v, list):
            # Filter out empty dicts or dicts missing the required 'statement' field
            valid_statements = []
            filtered_count = 0
            for i, stmt in enumerate(v):
                if isinstance(stmt, dict) and stmt.get('statement'):
                    valid_statements.append(stmt)
                elif isinstance(stmt, dict):
                    # Log which statement was filtered
                    filtered_count += 1
                    logger.debug(f"Filtering out invalid statement at index {i}: {stmt}")
            
            if filtered_count > 0:
                logger.warning(f"Filtered out {filtered_count} empty/invalid statements from LLM response")
            
            return valid_statements
        return v

class StatementExtractor:
    """Class for extracting statements from dialog chunks using LLM (relations separated)"""

    def __init__(self, llm_client: Any, config: StatementExtractionConfig = None):
        # 避免在测试收集阶段因为 OpenAIClient 间接引入 langfuse 导致 ModuleNotFoundError 。这只是类型注解与导入时机的调整，不改变实现。
        """Initialize the StatementExtractor with an LLM client and configuration

        Args:
            llm_client: OpenAIClient instance for processing LLM requests
            config: StatementExtractionConfig for controlling extraction behavior
        """
        self.llm_client = llm_client
        self.config = config or StatementExtractionConfig()

    async def _extract_statements(self, chunk, group_id: Optional[str] = None, dialogue_content: str = None) -> List[Statement]:
        """Process a single chunk and return extracted statements

        Args:
            chunk: Chunk object to process
            group_id: Group ID to assign to all statements in this chunk
            dialogue_content: Full dialogue content to provide as context

        Returns:
            List of ExtractedStatement objects extracted from the chunk
        """
        # Prepare the chunk content for processing
        chunk_content = chunk.content

        # Render the prompt using helper function
        prompt_content = await render_statement_extraction_prompt(
            chunk_content=chunk_content,
            definitions=LABEL_DEFINITIONS,
            json_schema=ExtractedStatement.model_json_schema(),
            granularity=self.config.statement_granularity,
            include_dialogue_context=self.config.include_dialogue_context,
            dialogue_content=dialogue_content,
            max_dialogue_chars=self.config.max_dialogue_context_chars
        )

        # Simple system message
        system_content = "You are an expert at extracting and labeling atomic statements from conversational text. Return valid JSON conforming to the schema."

        # Create messages for LLM
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt_content}
        ]

        try:
            # Get structured response from LLM (statements only)
            response = await self.llm_client.response_structured(messages, StatementExtractionResponse)
            # Defensive: ensure response has the expected structure
            if not hasattr(response, "statements") or response.statements is None:
                logger.warning("Invalid structured response: missing 'statements'. Returning empty list for this chunk.")
                return []

            # Convert extracted statements to Statement objects
            chunk_statements = []
            for extracted_stmt in response.statements:
                # Normalize and correct enums defensively
                stmt_type_str = str(extracted_stmt.statement_type).strip().upper()
                temporal_type_str = str(extracted_stmt.temporal_type).strip().upper()
                relevence_str = str(extracted_stmt.relevence).strip().upper()

                # Convert strings to enum types with fallback defaults
                try:
                    stmt_type = StatementType[stmt_type_str] if stmt_type_str in StatementType.__members__ else StatementType.FACT
                except (KeyError, ValueError):
                    stmt_type = StatementType.FACT

                try:
                    temporal_type = TemporalInfo[temporal_type_str] if temporal_type_str in TemporalInfo.__members__ else TemporalInfo.STATIC
                except (KeyError, ValueError):
                    temporal_type = TemporalInfo.STATIC

                try:
                    relevence_info = RelevenceInfo[relevence_str] if relevence_str in RelevenceInfo.__members__ else RelevenceInfo.RELEVANT
                except (KeyError, ValueError):
                    relevence_info = RelevenceInfo.RELEVANT

                chunk_statement = Statement(
                    statement=extracted_stmt.statement,
                    stmt_type=stmt_type,
                    temporal_info=temporal_type,
                    relevence_info=relevence_info,
                    chunk_id=chunk.id,
                    group_id=group_id,
                )
                chunk_statements.append(chunk_statement)

            # 分离强弱关系分类：不在句子提取阶段进行，也不写入 chunk.metadata
            return chunk_statements

        except Exception as e:
            logger.error(f"Error processing chunk: {e}", exc_info=True)
            # Return empty list to indicate failure for this chunk
            return []

    async def extract_statements(self, dialog_data: DialogData, limit_chunks: int = None) -> List[List[Statement]]:
        """Extract statements from a DialogData object.

        Args:
            dialog_data: The DialogData object containing chunks.
            limit_chunks: Optional limit on the number of chunks to process.
        """
        # Determine how many chunks to process
        chunks_to_process = dialog_data.chunks[:limit_chunks] if limit_chunks else dialog_data.chunks

        logger.info(f"Processing {len(chunks_to_process)} chunks for statement extraction")

        # Process all chunks concurrently, passing the group_id and dialogue content from dialog_data
        dialogue_content = dialog_data.content if self.config.include_dialogue_context else None
        results = await asyncio.gather(
            *[self._extract_statements(chunk, dialog_data.group_id, dialogue_content) for chunk in chunks_to_process],
            return_exceptions=True
        )

        # Filter out exceptions and return valid results
        valid_results = []
        for result in results:
            if isinstance(result, list) and result is not None:
                valid_results.append(result)
            else:
                print(f"Error in statement extraction: {result}")
                valid_results.append([])

        return valid_results

    def save_statements(self, statements: List[Statement], output_path: str = None) -> str:
        """Save the extracted statements to a file and return the output path.

        Args:
            statements: List of Statement objects to save
            output_path: Optional path to save the output (default: statement_extraction.txt)

        Returns:
            The path where the output was saved
        """
        # 使用全局配置的输出路径
        if not output_path:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            output_path = settings.get_memory_output_path("statement_extraction.txt")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"Extracted Statements ({len(statements)} total)\n")
            f.write("=" * 50 + "\n\n")

            for i, statement in enumerate(statements, 1):
                f.write(f"Statement {i}:\n")
                f.write(f"Id: {statement.id}\n")
                f.write(f"Group Id: {statement.group_id}\n")
                f.write(f"Content: {statement.statement}\n")
                f.write(f"Type: {statement.stmt_type.value}\n")
                f.write(f"Temporal Info: {statement.temporal_info.value}\n")
                f.write(f"Created At: {datetime.now()}\n")
                f.write(f"Expired At: {None}\n")
                f.write(f"Valid At: {statement.temporal_validity.valid_at if statement.temporal_validity else None}\n")
                f.write(f"Invalid At: {statement.temporal_validity.invalid_at if statement.temporal_validity else None}\n")
                f.write(f"Chunk Id: {statement.chunk_id}\n")
                # add relevance information to satisfy tests
                if hasattr(statement, "relevence_info") and statement.relevence_info is not None:
                    f.write(f"Relevence Info: {statement.relevence_info.value}\n")
                f.write("-" * 30 + "\n\n")

        print(f"Extracted {len(statements)} statements and saved to {output_path}")
        return output_path

    def save_relations(self, dialogs: List[DialogData], output_path: str = None) -> str:
        """按对话分组聚合强/弱关系并写入 TXT 文件。
        - 每个对话单独成段：输出该对话的 `Dialog ID`、`Group ID`、`Content`
        - 在该对话段内再分为 Strong Relations / Weak Relations 两部分
        - Strong: 逐条输出 `Chunk ID` 与 `Triple`
        - Weak: 逐条输出 `Chunk ID` 与 `Entity`
        """
        print("\n=== Relations Classify ===")

        # 使用全局配置的输出路径
        if not output_path:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            output_path = settings.get_memory_output_path("relations_output.txt")
            # output_path = os.path.join(os.path.dirname(__file__), "..", "relations_output.txt")

        dialog_sections: List[Dict[str, Any]] = []
        total_strong = 0
        total_weak = 0

        for dialog in dialogs:
            strong_relations: List[Dict[str, Any]] = []
            weak_relations: List[Dict[str, Any]] = []

            for chunk in dialog.chunks or []:
                # 基于三元组/实体推导强弱关系
                for stmt in chunk.statements or []:
                    te = getattr(stmt, "triplet_extraction_info", None)
                    if not te:
                        continue
                    trips = getattr(te, "triplets", []) or []
                    ents = getattr(te, "entities", []) or []

                    # Strong: 逐条输出三元组
                    if trips:
                        for trip in trips:
                            subj = getattr(trip, "subject_name", "")
                            pred = str(getattr(trip, "predicate", ""))
                            obj = getattr(trip, "object_name", "")
                            triple_str = f"({subj}, {pred}, {obj})"
                            strong_relations.append({
                                "chunk_id": chunk.id,
                                "triple": triple_str,
                            })
                    else:
                        # Weak: 无三元组但有实体
                        for ent in ents:
                            name = getattr(ent, "name", "")
                            desc = getattr(ent, "description", "") or ""
                            entity_str = f"{name}: {desc}" if desc else name
                            if name:
                                weak_relations.append({
                                    "chunk_id": chunk.id,
                                    "entity": entity_str,
                                })

            total_strong += len(strong_relations)
            total_weak += len(weak_relations)

            dialog_sections.append({
                "dialog_id": dialog.ref_id,
                "group_id": dialog.group_id,
                "content": dialog.content if getattr(dialog, "content", None) else "",
                "strong": strong_relations,
                "weak": weak_relations,
            })

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"Relations Extraction (grouped by dialogs, strong: {total_strong}, weak: {total_weak})\n")
                f.write("=" * 50 + "\n\n")

                for idx, section in enumerate(dialog_sections, 1):
                    f.write(f"Dialog {idx}:\n")
                    f.write(f"Dialog ID: {section.get('dialog_id', '')}\n")
                    f.write(f"Group ID: {section.get('group_id', '')}\n")
                    f.write("Content:\n")
                    f.write(f"{section.get('content', '')}\n")
                    f.write("-" * 40 + "\n\n")

                    # Strong Relations for this dialog
                    strong_list = section.get("strong", [])
                    f.write(f"Strong Relations ({len(strong_list)} total)\n")
                    f.write("-" * 30 + "\n\n")
                    for i, item in enumerate(strong_list, 1):
                        f.write(f"Item {i}:\n")
                        f.write(f"Chunk ID: {item.get('chunk_id', '')}\n")
                        f.write(f"Triple: {item.get('triple', '')}\n")
                        f.write("-" * 30 + "\n\n")

                    # Weak Relations for this dialog
                    weak_list = section.get("weak", [])
                    f.write(f"Weak Relations ({len(weak_list)} total)\n")
                    f.write("-" * 30 + "\n\n")
                    for i, item in enumerate(weak_list, 1):
                        f.write(f"Item {i}:\n")
                        f.write(f"Chunk ID: {item.get('chunk_id', '')}\n")
                        f.write(f"Entity: {item.get('entity', '')}\n")
                        f.write("-" * 30 + "\n\n")

            print(f"Saved relations to {output_path}")
            return output_path
        except Exception as e:
            print(f"Failed to save relations to {output_path}: {e}")
            return output_path
