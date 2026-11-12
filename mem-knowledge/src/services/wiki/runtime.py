"""Short transaction and existing model-runtime adapters."""

from copy import deepcopy

from ...rag.wiki.config import WikiError
from ...repositories.wiki.common import as_uuid


def transaction(runtime, operation, *args, **kwargs):
    with runtime.database.sync_session() as session:
        try:
            result = operation(session, *args, **kwargs)
            session.commit()
            return deepcopy(result)
        except Exception:
            session.rollback()
            raise


def make_model(runtime, snapshot):
    from ...integrations.model.invoke_backend import RemoteInvokeRef
    from ...rag.models.task_runtime import TaskModelFactory
    from ...rag.wiki.model import WikiModel

    model_id, tenant_id = as_uuid(snapshot["llm_id"]), as_uuid(snapshot["tenant_id"])
    try:
        view = TaskModelFactory(runtime).resolve_chat(model_id, tenant_id)
    except Exception as exc:
        from ...error_mapping import map_model_error

        mapped = map_model_error(exc)
        raise WikiError(mapped.code, retryable=mapped.retryable, diagnostics={
            "stage": "model_resolve", "model_id": str(model_id),
        }) from exc
    if str(view.profile.type) != "llm":
        raise WikiError("WIKI_MODEL_REQUIRED", retryable=False)
    return WikiModel(
        runtime,
        RemoteInvokeRef(
            config_id=model_id,
            tenant_id=tenant_id,
            workspace_id=as_uuid(snapshot["workspace_id"]),
        ),
    )
