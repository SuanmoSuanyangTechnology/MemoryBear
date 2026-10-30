"""Decision + audit in one call: the entry point an enforcement adapter uses."""

from __future__ import annotations

import logging

from ..clock import Clock
from .audit import AuditSink, EgressAuditEvent, EgressContext
from .rules import (
    REASON_AUDIT_UNAVAILABLE,
    Decision,
    EgressPolicy,
    EgressRequest,
    TenantRules,
    _deny,
)

logger = logging.getLogger(__name__)


class EgressGate:
    def __init__(self, policy: EgressPolicy, sink: AuditSink, clock: Clock | None = None) -> None:
        self._policy = policy
        self._sink = sink
        self._clock = clock or Clock()

    def decide(
        self, ctx: EgressContext, req: EgressRequest, tenant: TenantRules | None = None
    ) -> Decision:
        """Evaluate and record. If the audit record cannot be written, deny (fail closed)."""

        decision = self._policy.evaluate(req, tenant)
        try:
            self._sink.record(EgressAuditEvent(self._clock.now_ms(), ctx, req, decision))
        except Exception:
            logger.exception("egress audit sink failed; denying %s", req.host)
            return _deny(REASON_AUDIT_UNAVAILABLE)
        return decision
