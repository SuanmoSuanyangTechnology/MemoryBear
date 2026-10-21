"""Knowledge-base read slice for the workflow runtime.

Only the by-name lookup the runtime memory-store resolver needs; the full
monolith repository lives on the knowledge service side and is not part of
this service's write surface.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from ..models.knowledge_model import Knowledge
from ..models.models_model import ModelConfig

db_logger = logging.getLogger(__name__)


def knowledge_schema_load_options():
    def model_config_options(relationship_attr):
        return (selectinload(relationship_attr).selectinload(ModelConfig.model_base),)

    return (
        selectinload(Knowledge.created_user),
        *model_config_options(Knowledge.embedding),
        *model_config_options(Knowledge.reranker),
        *model_config_options(Knowledge.llm),
        *model_config_options(Knowledge.image2text),
        *model_config_options(Knowledge.audio2text),
        *model_config_options(Knowledge.video2text),
    )


def get_knowledge_by_name(db: Session, name: str, workspace_id: uuid.UUID) -> Knowledge | None:
    db_logger.debug(f"Query knowledge base based on name and workspace_id: name={name}, workspace_id={workspace_id}")

    try:
        knowledge = db.query(Knowledge).filter(Knowledge.name == name,
                                               Knowledge.workspace_id == workspace_id,
                                               Knowledge.status == 1).first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful: {name} (ID: {knowledge.id})")
        else:
            db_logger.debug(f"knowledge base does not exist: name={name}, workspace_id={workspace_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the name and workspace_id: name={name}, workspace_id={workspace_id} - {str(e)}")
        raise


async def get_knowledge_by_name_async(db: AsyncSession, name: str, workspace_id: uuid.UUID) -> Knowledge | None:
    """Async version of get_knowledge_by_name."""
    db_logger.debug(f"Query knowledge base based on name and workspace_id (async): name={name}, workspace_id={workspace_id}")

    try:
        stmt = (
            select(Knowledge)
            .options(*knowledge_schema_load_options())
            .where(Knowledge.name == name, Knowledge.workspace_id == workspace_id, Knowledge.status == 1)
        )
        result = await db.execute(stmt)
        knowledge = result.scalars().first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful (async): {name} (ID: {knowledge.id})")
        else:
            db_logger.debug(f"knowledge base does not exist (async): name={name}, workspace_id={workspace_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the name and workspace_id (async): {str(e)}")
        raise


__all__ = [
    "get_knowledge_by_name",
    "get_knowledge_by_name_async",
    "knowledge_schema_load_options",
]
