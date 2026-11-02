"""The inbound job registry: contract name -> application handler.

One entry per task this worker owns. The name comes from
``task_bridge.mem_router.TaskName`` — the contract memory-profile publishes
with — and the handler is built lazily from the composition root, so importing
this module touches no connection, driver or broker.

Adding a job means writing its handler under ``src/application/jobs`` and adding
exactly one entry here. ``celery_adapter`` renders the entries into Celery
tasks; another executor's adapter would read the same table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from task_bridge.mem_router import TaskName

from src.application.jobs import (
    FanOutWorkspaceJob,
    GenerateInterestDistributionJob,
    GenerateProfileCacheJob,
    PingJob,
    PrewarmInterestDistributionsJob,
    ScanProfileCachesJob,
)
from src.application.ports.jobs import JobHandler, JobSpec
from src.composition import get_container
from src.runtime import get_runtime


@dataclass(frozen=True, slots=True)
class JobRegistration:
    """One job's wire name, execution policy and lazy handler factory."""

    name: str
    #: Declared on the handler class so the adapter can read it without
    #: building the handler's dependencies.
    spec: JobSpec
    build: Callable[[], JobHandler]


JOBS: tuple[JobRegistration, ...] = (
    JobRegistration(
        name=TaskName.HEALTH_PING.value,
        spec=PingJob.spec,
        # The health job probes infrastructure by definition; the runtime is the
        # probe, and importing it here is the one deliberate exception.
        build=lambda: PingJob(get_runtime()),
    ),
    JobRegistration(
        name=TaskName.PROFILE_CACHE_GENERATE.value,
        spec=GenerateProfileCacheJob.spec,
        build=lambda: GenerateProfileCacheJob(
            get_container().profile_cache.generate()
        ),
    ),
    JobRegistration(
        name=TaskName.PROFILE_CACHE_GENERATE_WORKSPACE.value,
        spec=FanOutWorkspaceJob.spec,
        build=lambda: FanOutWorkspaceJob(
            get_container().profile_cache.fan_out
        ),
    ),
    JobRegistration(
        name=TaskName.PROFILE_CACHE_SCAN.value,
        spec=ScanProfileCachesJob.spec,
        build=lambda: ScanProfileCachesJob(
            get_container().profile_cache.scan()
        ),
    ),
    JobRegistration(
        name=TaskName.INTEREST_DISTRIBUTION_GENERATE.value,
        spec=GenerateInterestDistributionJob.spec,
        build=lambda: GenerateInterestDistributionJob(
            get_container().interest.generate()
        ),
    ),
    JobRegistration(
        name=TaskName.INTEREST_DISTRIBUTION_INIT_FOR_USERS.value,
        spec=PrewarmInterestDistributionsJob.spec,
        build=lambda: PrewarmInterestDistributionsJob(
            get_container().interest.prewarm
        ),
    ),
)
