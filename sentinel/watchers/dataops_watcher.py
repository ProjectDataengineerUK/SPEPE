from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sentinel.events.event_types import EventType, SentinelEvent, Severity

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    detected: bool
    reason: str
    metrics: dict[str, Any]


class DataOpsWatcher:
    """Monitors DQ gates, contract violations, freshness SLOs and job failures."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.freshness_slo_hours = self.config.get("freshness_slo_hours", 6)
        self.dq_pass_threshold = self.config.get("dq_pass_threshold", 0.95)

    def observe(self, event: SentinelEvent) -> Observation:
        if event.type == EventType.DQ_VIOLATION:
            return self._inspect_dq(event)
        if event.type == EventType.CONTRACT_BREACH:
            return self._inspect_contract(event)
        if event.type == EventType.FRESHNESS_SLO_BREACH:
            return self._inspect_freshness(event)
        if event.type == EventType.PIPELINE_FAILURE:
            return self._inspect_pipeline(event)
        return Observation(False, "not_dataops_event", {})

    def _inspect_dq(self, event: SentinelEvent) -> Observation:
        payload = event.payload
        pass_rate = float(payload.get("pass_rate", 0.0))
        failed_expectations = payload.get("failed_expectations", [])
        return Observation(
            detected=pass_rate < self.dq_pass_threshold,
            reason=f"dq_pass_rate={pass_rate:.3f} below {self.dq_pass_threshold}",
            metrics={
                "pass_rate": pass_rate,
                "failed_expectations": failed_expectations,
                "source": payload.get("source"),
                "layer": payload.get("layer", "silver"),
            },
        )

    def _inspect_contract(self, event: SentinelEvent) -> Observation:
        violations = event.payload.get("violations", [])
        return Observation(
            detected=len(violations) > 0,
            reason=f"contract_violations={len(violations)}",
            metrics={
                "contract": event.payload.get("contract_name"),
                "violations": violations,
            },
        )

    def _inspect_freshness(self, event: SentinelEvent) -> Observation:
        delay_hours = float(event.payload.get("delay_hours", 0))
        return Observation(
            detected=delay_hours > self.freshness_slo_hours,
            reason=f"freshness_delay={delay_hours:.1f}h slo={self.freshness_slo_hours}h",
            metrics={
                "delay_hours": delay_hours,
                "table": event.payload.get("table"),
            },
        )

    def _inspect_pipeline(self, event: SentinelEvent) -> Observation:
        exit_code = int(event.payload.get("exit_code", 0))
        return Observation(
            detected=exit_code != 0,
            reason=f"pipeline_exit_code={exit_code}",
            metrics={
                "job_id": event.payload.get("job_id"),
                "stage": event.payload.get("stage"),
                "exit_code": exit_code,
                "attempt": event.payload.get("attempt", 1),
            },
        )

    @staticmethod
    def severity_for(observation: Observation, event_type: EventType) -> Severity:
        if event_type == EventType.CONTRACT_BREACH:
            return Severity.P1
        if event_type == EventType.DQ_VIOLATION:
            pr = float(observation.metrics.get("pass_rate", 1.0))
            return Severity.P1 if pr < 0.80 else Severity.P2
        return Severity.P2
