"""Scheduler tunables (design doc 3.1 and Q2).

Every number marked "provisional" in the doc is a default here and is meant to be
recalibrated after the V9 capacity run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerConfig:
    # --- capacity / admission ---
    capacity: int = 50  # max instances (running + paused + creating) on this backend
    tenant_capacity: int | None = None  # hard per-tenant cap; None = unlimited
    tenant_soft_quota: int | None = None  # above this a tenant is evicted first
    max_kernels_per_user: int = 4

    # --- lifecycle (doc 3, Q2) ---
    idle_pause_s: float = 20 * 60  # A: idle -> pause
    max_retention_s: float = 24 * 3600  # B: paused this long -> destroy
    absolute_lifetime_s: float = 24 * 3600  # C: forced recreate
    p_min_s: float = 2 * 3600  # paused this long before it may be evicted (decided)
    # A conversation's kernel unused this long is deleted (a terminal kernel first kills
    # the process groups its commands started). 0 disables. Keep it below idle_pause_s.
    kernel_idle_s: float = 15 * 60

    # --- eviction watermarks (provisional) ---
    high_water: float = 0.90
    low_water: float = 0.75
    evict_batch_max: int = 10

    # --- queueing when full (provisional) ---
    queue_wait_s: float = 3.0
    queue_poll_s: float = 0.05

    # --- creation throttling ---
    create_concurrency: int = 4  # node-level semaphore
    create_max_per_window: int = 5  # per end user
    create_window_s: float = 60.0

    # --- housekeeping ---
    orphan_grace_s: float = 120.0
    tombstone_ttl_s: float = 7 * 24 * 3600
    tick_interval_s: float = 30.0  # how often the background runner calls ``tick``
    reconcile_interval_s: float = 300.0  # how often it calls ``reconcile``

    # --- execution ---
    default_template: str = "workspace"
    exec_timeout_default_s: float = 120.0
    exec_timeout_max_s: float = 300.0  # a request can only lower its timeout below this

    def __post_init__(self) -> None:
        if not 0 < self.exec_timeout_default_s <= self.exec_timeout_max_s:
            raise ValueError("need 0 < exec_timeout_default_s <= exec_timeout_max_s")
        if self.capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not 0 < self.low_water < self.high_water <= 1:
            raise ValueError("watermarks must satisfy 0 < low < high <= 1")
        if self.max_kernels_per_user < 1:
            raise ValueError("max_kernels_per_user must be >= 1")
        if self.evict_batch_max < 1:
            raise ValueError("evict_batch_max must be >= 1")
        if self.create_concurrency < 1 or self.create_max_per_window < 1:
            raise ValueError("create limits must be >= 1")
        for name in ("idle_pause_s", "max_retention_s", "absolute_lifetime_s", "p_min_s", "kernel_idle_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.queue_wait_s < 0 or self.queue_poll_s <= 0:
            raise ValueError("queue_wait_s must be >= 0 and queue_poll_s > 0")
        if self.tenant_capacity is not None and self.tenant_capacity < 1:
            raise ValueError("tenant_capacity must be >= 1")
        if self.tick_interval_s <= 0 or self.reconcile_interval_s <= 0:
            raise ValueError("tick_interval_s and reconcile_interval_s must be > 0")
