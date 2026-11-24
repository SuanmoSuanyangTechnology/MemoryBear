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
from typing import Any

from task_bridge.mem_router import TaskName

from src.application.jobs import (
    DispatchAdmissionJob,
    DoMetadataMigrationJob,
    FanOutWorkspaceJob,
    GenerateInterestDistributionJob,
    GenerateProfileCacheJob,
    OutboxCleanupJob,
    OutboxProjectionJob,
    PingJob,
    PrewarmInterestDistributionsJob,
    RecoverAdmissionsJob,
    ScanMetadataMigrationJob,
    ScanProfileCachesJob,
)
from src.application.jobs.conversation_detail_generate import GenerateConversationDetailJob
from src.application.jobs.emotion_suggestions_generate import GenerateEmotionSuggestionsJob
from src.application.jobs.forgetting_trigger import TriggerForgettingJob
from src.application.jobs.rag_profile_generate import GenerateRAGProfileJob
from src.application.ports.jobs import JobHandler, JobSpec
from src.composition import get_container
from src.extensions import extension_jobs, load_extensions
from src.runtime import get_runtime


@dataclass(frozen=True, slots=True)
class JobRegistration:
    """One job's wire name, execution policy and lazy handler factory."""

    name: str
    #: Declared on the handler class so the adapter can read it without
    #: building the handler's dependencies.
    spec: JobSpec
    build: Callable[[], JobHandler]


def _container() -> Any:
    """Keep the container lookup behind one name (tests patch it)."""

    return get_container()


JOBS: tuple[JobRegistration, ...] = (
    JobRegistration(
        name=TaskName.RAG_PROFILE_GENERATE.value,
        spec=GenerateRAGProfileJob.spec,
        build=lambda: GenerateRAGProfileJob(get_container().rag_profile.generate()),
    ),
    JobRegistration(
        name=TaskName.EMOTION_SUGGESTIONS_GENERATE.value,
        spec=GenerateEmotionSuggestionsJob.spec,
        build=lambda: GenerateEmotionSuggestionsJob(get_container().emotion.generate()),
    ),
    JobRegistration(
        name=TaskName.FORGETTING_TRIGGER.value,
        spec=TriggerForgettingJob.spec,
        build=lambda: TriggerForgettingJob(get_container().forgetting.trigger()),
    ),
    JobRegistration(
        name=TaskName.WORKING_MEMORY_DETAIL_GENERATE.value,
        spec=GenerateConversationDetailJob.spec,
        build=lambda: GenerateConversationDetailJob(
            get_container().working_memory.generate()
        ),
    ),
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
        build=lambda: GenerateProfileCacheJob(_container().profile_cache.generate()),
    ),
    JobRegistration(
        name=TaskName.PROFILE_CACHE_GENERATE_WORKSPACE.value,
        spec=FanOutWorkspaceJob.spec,
        build=lambda: FanOutWorkspaceJob(_container().profile_cache.fan_out),
    ),
    JobRegistration(
        name=TaskName.PROFILE_CACHE_SCAN.value,
        spec=ScanProfileCachesJob.spec,
        build=lambda: ScanProfileCachesJob(_container().profile_cache.scan()),
    ),
    JobRegistration(
        name=TaskName.METADATA_MIGRATION_SCAN.value,
        spec=ScanMetadataMigrationJob.spec,
        build=lambda: ScanMetadataMigrationJob(get_container().metadata_migration.scan()),
    ),
    JobRegistration(
        name=TaskName.METADATA_MIGRATION_DO.value,
        spec=DoMetadataMigrationJob.spec,
        build=lambda: DoMetadataMigrationJob(get_container().metadata_migration.do()),
    ),
    JobRegistration(
        name=TaskName.INTEREST_DISTRIBUTION_GENERATE.value,
        spec=GenerateInterestDistributionJob.spec,
        build=lambda: GenerateInterestDistributionJob(_container().interest.generate()),
    ),
    JobRegistration(
        name=TaskName.INTEREST_DISTRIBUTION_INIT_FOR_USERS.value,
        spec=PrewarmInterestDistributionsJob.spec,
        build=lambda: PrewarmInterestDistributionsJob(_container().interest.prewarm),
    ),
    JobRegistration(
        name=TaskName.ADMISSION_DISPATCH.value,
        spec=DispatchAdmissionJob.spec,
        build=lambda: DispatchAdmissionJob(_container().admission.dispatch()),
    ),
    JobRegistration(
        name=TaskName.ADMISSION_RECOVER.value,
        spec=RecoverAdmissionsJob.spec,
        build=lambda: RecoverAdmissionsJob(_container().admission.recover()),
    ),
    JobRegistration(
        name=TaskName.OUTBOX_PROJECTION.value,
        spec=OutboxProjectionJob.spec,
        build=lambda: OutboxProjectionJob(_container().outbox.projection()),
    ),
    JobRegistration(
        name=TaskName.OUTBOX_CLEANUP.value,
        spec=OutboxCleanupJob.spec,
        build=lambda: OutboxCleanupJob(_container().outbox.cleanup()),
    ),
)


def get_jobs() -> tuple[JobRegistration, ...]:
    """The open-build jobs followed by every configured extension job."""

    return JOBS + tuple(
        JobRegistration(name=job.name, spec=job.spec, build=job.build)
        for job in extension_jobs(load_extensions())
    )
