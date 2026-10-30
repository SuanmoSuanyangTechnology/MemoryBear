"""Inbound jobs: one handler per task contract.

A handler is the application's side of a worker task — payload validation, the
use case call, the result object and the failure policy. It knows nothing about
the executor: no Celery import, no ``self.request``, no time limits, and no
decision about the keys a caller sees. The executor adapters under
``src/interfaces/`` own the job registry, the attempt token, the transport
policy and the response serialization, so another executor reuses these handlers
as-is.

Adding a job means a handler module here plus one entry in
``src/interfaces/tasks/registry.py``.
"""

from src.application.jobs.health_ping import PingJob
from src.application.jobs.interest_generate import GenerateInterestDistributionJob
from src.application.jobs.interest_prewarm import PrewarmInterestDistributionsJob
from src.application.jobs.profile_cache_generate import GenerateProfileCacheJob
from src.application.jobs.profile_cache_generate_workspace import (
    FanOutWorkspaceJob,
)
from src.application.jobs.profile_cache_scan import ScanProfileCachesJob

__all__ = [
    "FanOutWorkspaceJob",
    "GenerateInterestDistributionJob",
    "GenerateProfileCacheJob",
    "PingJob",
    "PrewarmInterestDistributionsJob",
    "ScanProfileCachesJob",
]
