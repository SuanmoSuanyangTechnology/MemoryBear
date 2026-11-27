from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.knowledge_model import Knowledge, PermissionType
from app.models.knowledgeshare_model import KnowledgeShare
from app.models.models_model import ModelConfig, ModelType
from app.models.skill_model import Skill
from app.models.tool_model import ToolConfig


def available_knowledge_ids(
    db: Session,
    ids: set[UUID],
    workspace_id: UUID,
    source_workspace_id: UUID | None = None,
) -> dict[UUID, UUID]:
    """Resolve request IDs to active knowledge bases usable in the target workspace."""
    if not ids:
        return {}

    source = aliased(Knowledge)
    shared = (
        select(KnowledgeShare.target_kb_id)
        .join(source, source.id == KnowledgeShare.source_kb_id)
        .where(
            KnowledgeShare.target_workspace_id == workspace_id,
            source.workspace_id == KnowledgeShare.source_workspace_id,
            source.status == 1,
        )
    )
    direct = select(Knowledge.id).where(
        Knowledge.id.in_(ids),
        Knowledge.workspace_id == workspace_id,
        Knowledge.status == 1,
        or_(
            Knowledge.permission_id == PermissionType.Private,
            and_(
                Knowledge.permission_id == PermissionType.Share,
                Knowledge.id.in_(shared),
            ),
        ),
    )
    resolved = {kb_id: kb_id for kb_id in db.scalars(direct)}
    remaining = ids - resolved.keys()
    if not remaining or source_workspace_id is None:
        return resolved

    previous_share = aliased(KnowledgeShare)
    matches = (
        select(
            KnowledgeShare.source_kb_id,
            previous_share.target_kb_id,
            KnowledgeShare.target_kb_id,
        )
        .join(Knowledge, Knowledge.id == KnowledgeShare.target_kb_id)
        .join(source, source.id == KnowledgeShare.source_kb_id)
        .outerjoin(
            previous_share,
            and_(
                previous_share.source_kb_id == KnowledgeShare.source_kb_id,
                previous_share.target_workspace_id == source_workspace_id,
                previous_share.target_kb_id.in_(remaining),
            ),
        )
        .where(
            KnowledgeShare.target_workspace_id == workspace_id,
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.permission_id == PermissionType.Share,
            source.workspace_id == KnowledgeShare.source_workspace_id,
            source.status == 1,
            or_(
                and_(
                    KnowledgeShare.source_workspace_id == source_workspace_id,
                    KnowledgeShare.source_kb_id.in_(remaining),
                ),
                previous_share.target_kb_id.is_not(None),
            ),
        )
    )
    candidates: dict[UUID, set[UUID]] = {}
    for source_id, previous_id, target_id in db.execute(matches):
        for reference in (source_id, previous_id):
            if reference in remaining:
                candidates.setdefault(reference, set()).add(target_id)
    for reference, targets in candidates.items():
        if len(targets) == 1:
            resolved[reference] = next(iter(targets))
    return resolved


def available_skill_ids(db: Session, ids: set[UUID], tenant_id: UUID) -> set[UUID]:
    if not ids:
        return set()
    return set(
        db.scalars(
            select(Skill.id).where(
                Skill.id.in_(ids),
                or_(Skill.tenant_id == tenant_id, Skill.is_public.is_(True)),
                Skill.is_active.is_(True),
            )
        )
    )


def tenant_tool_ids(
    db: Session, ids: set[UUID], tenant_id: UUID, *, active_only: bool = False
) -> set[UUID]:
    if not ids:
        return set()
    stmt = select(ToolConfig.id).where(
        ToolConfig.id.in_(ids), ToolConfig.tenant_id == tenant_id
    )
    if active_only:
        stmt = stmt.where(ToolConfig.is_active.is_(True))
    return set(db.scalars(stmt))


def available_reranker_ids(db: Session, ids: set[UUID], tenant_id: UUID) -> set[UUID]:
    if not ids:
        return set()
    return set(
        db.scalars(
            select(ModelConfig.id).where(
                ModelConfig.id.in_(ids),
                or_(
                    ModelConfig.tenant_id == tenant_id, ModelConfig.is_public.is_(True)
                ),
                ModelConfig.is_active.is_(True),
                ModelConfig.type == ModelType.RERANK.value,
            )
        )
    )
