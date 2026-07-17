import asyncio
import uuid
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.logging_config import get_memory_logger
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.utils.prompt.prompt_utils import render_triplet_extraction_prompt
from app.core.memory.utils.data.ontology import PREDICATE_DEFINITIONS
from app.core.memory.models.triplet_models import TripletExtractionResponse
from app.core.memory.models.message_models import DialogData, Statement
from app.core.memory.models.ontology_extraction_models import OntologyTypeList
from app.core.memory.utils.log.logging_utils import prompt_logger

logger = get_memory_logger(__name__)


class TripletExtractor:
    """Extracts knowledge triplets and entities from statements using LLM"""

    def __init__(
            self,
            db: Session,
            model_id: uuid.UUID,
            tenant_id: uuid.UUID,
            ontology_types: Optional[OntologyTypeList] = None,
            language: str = "zh"
    ):
        """Initialize the TripletExtractor with an LLM client

        Args:
            db: 数据库 session
            model_id: LLM 模型 ID
            tenant_id: 租户 ID
            language: 语言类型 ("zh" 中文, "en" 英文)，默认中文
            ontology_types: Optional OntologyTypeList containing predefined ontology types
                for entity classification guidance
        """
        self.llm_client = ModelClientMixin.get_llm_client(db, model_id, tenant_id)
        self.ontology_types = ontology_types
        self.language = language

    def _get_language(self) -> str:
        """Get the configured language for entity descriptions
        
        Returns:
            Language code ("zh" or "en")
        """
        return self.language

    async def _extract_triplets(self, statement: Statement, chunk_content: str) -> TripletExtractionResponse:
        """Process a single statement and return extracted triplets and entities"""
        # Render the prompt using helper function
        # Log start and input context similar to legacy logs
        try:
            prompt_logger.info(f"[Triplet] Started - statement_id={statement.id}")
            prompt_logger.debug(f"[Triplet] Input statement=\"{statement.statement}\"")
        except Exception:
            # Avoid breaking flow due to logging issues
            pass

        prompt_content = await render_triplet_extraction_prompt(
            statement=statement.statement,
            chunk_content=chunk_content,
            json_schema=TripletExtractionResponse.model_json_schema(),
            predicate_instructions=PREDICATE_DEFINITIONS,
            language=self._get_language(),
            ontology_types=self.ontology_types,
            speaker=getattr(statement, 'speaker', None),
        )

        # Create messages for LLM
        messages = [
            {"role": "system",
             "content": "You are an expert at extracting knowledge triplets and entities from text. Follow the provided instructions carefully and return valid JSON."},
            {"role": "user", "content": prompt_content}
        ]

        try:
            # Get structured response from LLM
            response = await self.llm_client.call_structured(messages, TripletExtractionResponse)
            # Create new triplets with statement_id set during creation
            updated_triplets = []
            for triplet in response.triplets:
                updated_triplet = triplet.model_copy(update={"statement_id": statement.id})
                updated_triplets.append(updated_triplet)

            # Log completion and per-item details to match legacy format
            try:
                prompt_logger.info(
                    f"[Triplet] Completed - statement_id={statement.id}, triplets={len(updated_triplets)}, entities={len(response.entities)}"
                )
                for i, t in enumerate(updated_triplets, 1):
                    prompt_logger.debug(
                        f"[Triplet] Triplet #{i}: ({t.subject_name}) - {t.predicate} - ({t.object_name}) value={t.value if t.value is not None else 'None'}"
                    )
                for i, e in enumerate(response.entities, 1):
                    prompt_logger.debug(
                        f"[Triplet] Entity #{i}: id={getattr(e, 'entity_idx', None)} name={getattr(e, 'name', None)} type={getattr(e, 'type', None)} desc={getattr(e, 'description', None)}"
                    )
            except Exception:
                print(f"Error logging triplet details: {e}")
                pass

            # Return new response with updated triplets
            return TripletExtractionResponse(
                triplets=updated_triplets,
                entities=response.entities
            )
            # # Set statement_id for each triplet to establish parent relationship
            # for triplet in response.triplets:
            #     triplet.statement_id = statement.id

            # return response

        except Exception as e:
            logger.error(f"Error processing statement: {e}", exc_info=True)
            return TripletExtractionResponse(triplets=[], entities=[])

    async def extract_triplets_from_statements(self, dialog_data: DialogData, limit_chunks: int = None) -> Dict[
        str, TripletExtractionResponse]:
        """Extract triplets and entities from statements

        Args:
            dialog_data: DialogData object to process
            limit_chunks: Number of chunks to process

        Returns:
            Dict[str, TripletExtractionResponse]: Dictionary mapping statement IDs to their triplet responses
        """
        # Collect all statements from the specified chunks
        all_statements = []
        chunks_to_process = dialog_data.chunks[:limit_chunks] if limit_chunks else dialog_data.chunks

        for chunk in chunks_to_process:
            all_statements.extend(chunk.statements)

        logger.info(f"Processing {len(all_statements)} statements for triplet extraction...")
        try:
            prompt_logger.info(
                f"[Triplet] Dialog ref_id={getattr(dialog_data, 'ref_id', None)}, end_user_id={getattr(dialog_data, 'end_user_id', None)}, statements_to_process={len(all_statements)}"
            )
        except Exception:
            pass

        # Prepare tasks and statement IDs
        tasks = []
        statement_ids = []

        for chunk in chunks_to_process:
            for statement in chunk.statements:
                tasks.append(self._extract_triplets(statement, chunk.content))
                statement_ids.append(statement.id)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Map results to statement IDs
        statement_triplet_map = {}
        for i, result in enumerate(results):
            statement_id = statement_ids[i]
            if isinstance(result, TripletExtractionResponse):
                statement_triplet_map[statement_id] = result
            else:
                logger.error(f"Error in triplet extraction for statement {statement_id}: {result}", exc_info=True)
                statement_triplet_map[statement_id] = TripletExtractionResponse(triplets=[], entities=[])

        # Dialog-level summary and details (match legacy format)
        try:
            # Flatten totals
            all_triplets = []
            all_entities_with_stmt = []
            for sid, resp in statement_triplet_map.items():
                for t in resp.triplets:
                    all_triplets.append(t)
                for e in resp.entities:
                    all_entities_with_stmt.append((sid, e))

            prompt_logger.info(
                f"[Triplet] Dialog ref_id={getattr(dialog_data, 'ref_id', None)} completed, total_triplets={len(all_triplets)}, total_entities={len(all_entities_with_stmt)}"
            )

            # Triplets Detail section
            prompt_logger.info("\n--- Triplets Detail ---")
            for i, t in enumerate(all_triplets, 1):
                prompt_logger.info(
                    f"[Triplet] #{i} statement_id={getattr(t, 'statement_id', None)} subject=({getattr(t, 'subject_name', None)}:{getattr(t, 'subject_id', None)}) predicate={getattr(t, 'predicate', None)} object=({getattr(t, 'object_name', None)}:{getattr(t, 'object_id', None)}) value={getattr(t, 'value', None) if getattr(t, 'value', None) is not None else 'None'}"
                )

            # Entities Detail section
            prompt_logger.info("\n--- Entities Detail ---")
            for i, (sid, e) in enumerate(all_entities_with_stmt, 1):
                prompt_logger.info(
                    f"[Entity] #{i} statement_id={sid} id={getattr(e, 'entity_idx', None)} name={getattr(e, 'name', None)} type={getattr(e, 'type', None)} desc={getattr(e, 'description', None)}"
                )
        except Exception:
            pass

        return statement_triplet_map

