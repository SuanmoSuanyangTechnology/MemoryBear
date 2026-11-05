"""Contracts shared between the API host, memory-profile, and memory-jobs.

Layout: one subpackage per domain (``preferences`` today, more as endpoints
migrate). A subpackage may contain plain constants, Pydantic DTOs, and pure
functions only — no database, HTTP, cache, or runtime imports — so every
service can depend on it without inheriting infrastructure choices.
"""

__all__: list[str] = []