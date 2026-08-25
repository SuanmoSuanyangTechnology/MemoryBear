"""QA import Celery task envelope."""

from __future__ import annotations

from ..runtime import get_worker_runtime
from .celery_app import celery_app


def process_qa_import(*args, **kwargs):
    """Load the model-heavy QA processor only when the task executes."""

    from ..services.qa_import_processing import process_qa_import as process

    return process(*args, **kwargs)


@celery_app.task(name="app.core.rag.tasks.import_qa_chunks", queue="qa_import")
def import_qa_chunks(
    kb_id: str,
    document_id: str,
    filename: str,
    contents: bytes | None = None,
    file_key: str | None = None,
    clear_parse_task: bool = False,
):
    return process_qa_import(
        get_worker_runtime(),
        kb_id,
        document_id,
        filename,
        contents=contents,
        file_key=file_key,
        clear_parse_task=clear_parse_task,
    )


__all__ = ["import_qa_chunks"]
