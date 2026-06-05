import uuid

from sqlalchemy.orm import Session

from app.models import document_model, knowledge_model
from app.models.user_model import User
from app.repositories import workspace_repository


def has_current_workspace_access(
    db: Session,
    current_user: User,
) -> bool:
    """检查当前用户是否可访问自己的 current workspace。"""
    if not current_user.current_workspace_id:
        return False

    workspace = workspace_repository.get_workspace_by_id(
        db=db,
        workspace_id=current_user.current_workspace_id,
    )
    if not workspace:
        return False

    if current_user.is_superuser:
        return current_user.tenant_id == workspace.tenant_id

    member = workspace_repository.get_member_in_workspace(
        db=db,
        user_id=current_user.id,
        workspace_id=current_user.current_workspace_id,
    )
    return member is not None


def require_current_workspace_knowledge(
    db: Session,
    knowledge_id: uuid.UUID,
    current_user: User,
):
    """验证知识库存在且属于当前 workspace。"""
    if not has_current_workspace_access(db=db, current_user=current_user):
        return None

    return (
        db.query(knowledge_model.Knowledge)
        .filter(
            knowledge_model.Knowledge.id == knowledge_id,
            knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
        )
        .first()
    )


def require_current_workspace_document(
    db: Session,
    document_id: uuid.UUID,
    current_user: User,
):
    """验证文档存在且所属知识库在当前 workspace。"""
    db_document = db.query(document_model.Document).filter(document_model.Document.id == document_id).first()
    if not db_document:
        return None

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=db_document.kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        return None

    return db_document
