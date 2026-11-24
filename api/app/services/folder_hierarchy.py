"""Validate folder creation and structural edits in their write transaction."""

from collections import defaultdict

from error_core.codes import BizCode
from error_core.knowledge import knowledge_message
from sqlalchemy import literal, or_, select, text

from app.core.exceptions import ValidationException
from app.i18n.exceptions import get_current_locale
from app.models.file_model import File
from app.models.knowledge_model import Knowledge


def validate_knowledge_hierarchy(db, workspace_id, values, max_depth, knowledge_id=None):
    _validate_hierarchy(
        db, Knowledge, Knowledge.workspace_id, workspace_id, Knowledge.type, "Folder",
        values, max_depth, knowledge_id, "knowledge",
        "KB_KNOWLEDGE_FOLDER_PARENT_INVALID", "KB_KNOWLEDGE_FOLDER_DEPTH_EXCEEDED",
    )


def validate_file_hierarchy(db, kb_id, values, max_depth, file_id=None):
    _validate_hierarchy(
        db, File, File.kb_id, kb_id, File.file_ext, "folder",
        values, max_depth, file_id, "file",
        "KB_VALIDATION_ERROR", "KB_FILE_FOLDER_DEPTH_EXCEEDED",
    )


def _validate_hierarchy(
    db, model, scope_column, root_id, kind_column, folder_kind,
    values, max_depth, node_id, tree_kind, invalid_code, depth_code,
):
    if node_id is not None and not {"parent_id", "status", kind_column.key} & values.keys():
        return
    # Both services use the same lock key. It is held until commit or rollback.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{tree_kind}:hierarchy:{root_id}"},
    )
    rows = db.execute(
        select(
            model.id, model.parent_id, kind_column.label("kind"),
            getattr(model, "status", literal(1)).label("status"),
        ).where(
            scope_column == root_id,
            or_(kind_column == folder_kind, model.id == node_id),
        )
    ).all()
    nodes = {row.id: row for row in rows}
    current = nodes.get(node_id)
    if node_id is not None and current is None:
        _reject(invalid_code, "The edited record is outside the folder tree.")
    parent_id = values.get("parent_id", current.parent_id if current else root_id)
    kind = values.get(kind_column.key, current.kind if current else None)
    parent_id = root_id if parent_id is None else parent_id
    old_parent_id = root_id if current is None or current.parent_id is None else current.parent_id
    restoring = current is not None and current.status == 2 and values.get("status", 2) != 2
    if current and parent_id == old_parent_id and kind == current.kind and not restoring:
        return

    def parent_depth(parent, *, check_available):
        seen = {node_id} if node_id is not None else set()
        depth = 0
        while parent not in (None, root_id):
            ancestor = nodes.get(parent)
            if parent in seen or ancestor is None or ancestor.kind != folder_kind:
                _reject(invalid_code, "The parent chain contains a cycle or an invalid folder.")
            if check_available and ancestor.status == 2:
                _reject(invalid_code, "A deleted folder cannot be used as the parent.")
            seen.add(parent)
            depth += 1
            parent = ancestor.parent_id
        return depth

    destination_depth = parent_depth(parent_id, check_available=True)
    if kind != folder_kind:
        if current and current.kind == folder_kind and db.scalar(
            select(model.id).where(
                scope_column == root_id, model.parent_id == node_id,
            ).limit(1)
        ) is not None:
            _reject(invalid_code, "A folder with children cannot be converted to a non-folder.")
        return

    # Include soft-deleted folders so later restoration cannot expand this subtree.
    children = defaultdict(list)
    for row in rows:
        if row.kind == folder_kind:
            children[row.parent_id].append(row.id)
    height = 1
    pending = [(node_id, 1)] if current else []
    visited = set()
    while pending:
        descendant_id, level = pending.pop()
        if descendant_id in visited:
            _reject(invalid_code, "The folder subtree contains a cycle.")
        visited.add(descendant_id)
        height = max(height, level)
        pending.extend((child, level + 1) for child in children[descendant_id])

    deepest = destination_depth + height
    if deepest <= max_depth:
        return
    if current and current.kind == folder_kind and not restoring:
        previous_deepest = parent_depth(old_parent_id, check_available=False) + height
        if deepest < previous_deepest:
            return
    _reject(depth_code, "The resulting folder subtree exceeds the configured depth.")


def _reject(code, diagnostic):
    biz_code = BizCode.VALIDATION_FAILED if code == "KB_VALIDATION_ERROR" else BizCode[code]
    raise ValidationException(
        knowledge_message(biz_code, get_current_locale() or "zh"),
        context={"reason": diagnostic},
    )
