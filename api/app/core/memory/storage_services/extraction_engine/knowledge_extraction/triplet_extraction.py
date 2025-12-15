import os
import asyncio
from typing import List, Dict

from app.core.logging_config import get_memory_logger
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.prompt.prompt_utils import render_triplet_extraction_prompt
from app.core.memory.utils.data.ontology import PREDICATE_DEFINITIONS, Predicate # 引入枚举 Predicate 白名单过滤
from app.core.memory.models.triplet_models import TripletExtractionResponse
from app.core.memory.models.message_models import DialogData, Statement
from app.core.memory.utils.log.logging_utils import prompt_logger

logger = get_memory_logger(__name__)



class TripletExtractor:
    """Extracts knowledge triplets and entities from statements using LLM"""

    def __init__(self, llm_client: OpenAIClient):
        """Initialize the TripletExtractor with an LLM client

        Args:
            llm_client: OpenAIClient instance for processing
        """
        self.llm_client = llm_client

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
            predicate_instructions=PREDICATE_DEFINITIONS
        )

        # Create messages for LLM
        messages = [
            {"role": "system", "content": "You are an expert at extracting knowledge triplets and entities from text. Follow the provided instructions carefully and return valid JSON."},
            {"role": "user", "content": prompt_content}
        ]

        try:
            # Get structured response from LLM
            response = await self.llm_client.response_structured(messages, TripletExtractionResponse)
            # Filter triplets to only allowed predicates from ontology
            # 这里过滤掉了不在 Predicate 枚举中的谓语 但是容易造成谓语太严格，有点语句的谓语没有在枚举中，就被判断为弱关系
            allowed_predicates = {p.value for p in Predicate}
            filtered_triplets = [t for t in response.triplets if getattr(t, "predicate", "") in allowed_predicates]
            # 仅保留predicate ∈ Predicate 的三元组，其余全部剔除

            # Create new triplets with statement_id set during creation
            updated_triplets = []
            for triplet in filtered_triplets:  # 仅保留 predicate ∈ Predicate 的三元组
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

    async def extract_triplets_from_statements(self, dialog_data: DialogData, limit_chunks: int = None) -> Dict[str, TripletExtractionResponse]:
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
                f"[Triplet] Dialog ref_id={getattr(dialog_data, 'ref_id', None)}, group_id={getattr(dialog_data, 'group_id', None)}, statements_to_process={len(all_statements)}"
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

    def save_triplets(self, triplet_responses: List[TripletExtractionResponse], output_path: str = None) -> str:
        """Save extracted triplets and entities to a file

        Args:
            triplet_responses: List of TripletExtractionResponse objects
            output_path: Optional path to save the results

        Returns:
            Path where the triplets were saved
        """
        if output_path is None:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            output_path = settings.get_memory_output_path("extracted_triplets.txt")

        # Flatten all triplets and entities
        all_triplets = []
        all_entities = []

        for response in triplet_responses:
            all_triplets.extend(response.triplets)
            all_entities.extend(response.entities)

        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"=== EXTRACTED TRIPLETS ({len(all_triplets)} total) ===\n\n")
            for i, triplet in enumerate(all_triplets, 1):
                f.write(f"Triplet {i}:\n")
                f.write(f"  Subject: {triplet.subject_name} (ID: {triplet.subject_id})\n")
                f.write(f"  Predicate: {triplet.predicate}\n")
                f.write(f"  Object: {triplet.object_name} (ID: {triplet.object_id})\n")
                if triplet.value:
                    f.write(f"  Value: {triplet.value}\n")
                f.write("\n")

            f.write(f"\n=== EXTRACTED ENTITIES ({len(all_entities)} total) ===\n\n")
            for i, entity in enumerate(all_entities, 1):
                f.write(f"Entity {i}:\n")
                f.write(f"  ID: {entity.entity_idx}\n")
                f.write(f"  Name: {entity.name}\n")
                f.write(f"  Type: {entity.type}\n")
                f.write(f"  Description: {entity.description}\n")
                f.write("\n")

        logger.info(f"Saved {len(all_triplets)} triplets and {len(all_entities)} entities to: {output_path}")
        return output_path
