"""Cross-domain shared services.

Everything here is used by more than one feature package: ``model_config``
resolves the workspace LLM, and ``repositories`` holds the per-aggregate data
access the features share. Domain-specific behaviour stays in its own package.
"""
