"""Business modules, one package per function.

Each feature package owns its algorithm, prompt templates and prompt loader.
Cross-domain code lives in ``shared``: ``shared.model_config`` resolves the
workspace LLM and ``shared.repositories`` holds the per-aggregate data access.
Celery adapters stay in ``src.tasks`` so this package never depends on the task
runtime.
"""
