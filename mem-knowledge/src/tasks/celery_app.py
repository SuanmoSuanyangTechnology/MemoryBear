"""Celery routing contract for knowledge workers."""

from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue

from ..bootstrap import get_settings
from ..config import KnowledgeSettings
from ..rag.faq.contracts import FAQ_GENERATE_TASK
from ..rag.wiki.config import BACKFILL_TASK, MAP_TASK, REDUCE_TASK, WIKI_QUEUE

KNOWLEDGE_QUEUES = (
    "document_tasks",
    "graphrag_tasks",
    "qa_import",
    WIKI_QUEUE,
)

PUBLISHABLE_KNOWLEDGE_TASK_ROUTES = {
    FAQ_GENERATE_TASK: WIKI_QUEUE,
    BACKFILL_TASK: WIKI_QUEUE,
    MAP_TASK: WIKI_QUEUE,
    REDUCE_TASK: WIKI_QUEUE,
    "app.core.rag.tasks.parse_document": "document_tasks",
    "app.core.rag.tasks.sync_knowledge_for_kb": "document_tasks",
    "app.core.rag.tasks.import_qa_chunks": "qa_import",
    "app.core.rag.tasks.sync_evidence_graph_document": "graphrag_tasks",
    "app.core.rag.tasks.rebuild_evidence_graph_knowledge": "graphrag_tasks",
    "app.core.rag.tasks.clear_all_knowledge_graph_data": "graphrag_tasks",
}

COMPATIBILITY_TASK_ROUTES = {
    "app.core.rag.tasks.build_graphrag_for_kb": "graphrag_tasks",
    "app.core.rag.tasks.build_graphrag_for_document": "graphrag_tasks",
    "app.core.rag.tasks.migrate_evidence_graph_knowledge": "graphrag_tasks",
}

KNOWLEDGE_TASK_ROUTES = {
    **PUBLISHABLE_KNOWLEDGE_TASK_ROUTES,
    **COMPATIBILITY_TASK_ROUTES,
}


def create_celery_app(settings: KnowledgeSettings) -> Celery:
    """Construct the routing app without connecting to the broker."""

    application = Celery(
        "kb",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )
    application.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_ignore_result=False,
        task_time_limit=settings.kb_task_time_limit_seconds,
        task_soft_time_limit=settings.kb_task_soft_time_limit_seconds,
        worker_prefetch_multiplier=settings.kb_worker_prefetch_multiplier,
        worker_redirect_stdouts_level="INFO",
        result_expires=settings.kb_result_expires_seconds,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_transport_options={"confirm_publish": True},
        task_publish_retry=True,
        task_publish_retry_policy={
            "max_retries": 3,
            "interval_start": 0.2,
            "interval_step": 0.2,
            "interval_max": 1,
        },
        worker_disable_rate_limits=True,
        worker_send_task_events=True,
        task_send_sent_event=True,
        task_default_queue="document_tasks",
        task_create_missing_queues=False,
        task_queues=tuple(
            Queue(
                name,
                Exchange(name, type="direct"),
                routing_key=name,
            )
            for name in KNOWLEDGE_QUEUES
        ),
        task_routes={
            task_name: {"queue": queue_name}
            for task_name, queue_name in KNOWLEDGE_TASK_ROUTES.items()
        },
    )
    return application


celery_app = create_celery_app(get_settings())

__all__ = [
    "COMPATIBILITY_TASK_ROUTES",
    "KNOWLEDGE_QUEUES",
    "KNOWLEDGE_TASK_ROUTES",
    "PUBLISHABLE_KNOWLEDGE_TASK_ROUTES",
    "celery_app",
    "create_celery_app",
]
