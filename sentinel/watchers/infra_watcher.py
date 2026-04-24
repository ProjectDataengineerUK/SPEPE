from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sentinel.events.event_types import EventType, SentinelEvent, Severity


@dataclass
class Observation:
    detected: bool
    reason: str
    metrics: dict[str, Any]


class InfraWatcher:
    """Monitors Cloud Run health, budget consumption and API p99 latency."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.api_p99_ms_threshold = self.config.get("api_p99_ms_threshold", 1500)
        self.budget_warn_pct = self.config.get("budget_warn_pct", 0.75)

    def observe(self, event: SentinelEvent) -> Observation:
        if event.type == EventType.BUDGET_WARNING:
            return self._inspect_budget(event)
        if event.type == EventType.PIPELINE_FAILURE:
            return self._inspect_service(event)
        return Observation(False, "not_infra_event", {})

    def _inspect_budget(self, event: SentinelEvent) -> Observation:
        consumed_pct = float(event.payload.get("consumed_pct", 0.0))
        return Observation(
            detected=consumed_pct >= self.budget_warn_pct,
            reason=f"budget_consumed={consumed_pct:.2%}",
            metrics={
                "consumed_pct": consumed_pct,
                "project": event.payload.get("project"),
                "currency": event.payload.get("currency", "BRL"),
            },
        )

    def _inspect_service(self, event: SentinelEvent) -> Observation:
        latency_ms = float(event.payload.get("p99_latency_ms", 0))
        error_rate = float(event.payload.get("error_rate", 0.0))
        degraded = latency_ms > self.api_p99_ms_threshold or error_rate > 0.05
        return Observation(
            detected=degraded,
            reason=f"p99={latency_ms}ms err={error_rate:.2%}",
            metrics={
                "p99_latency_ms": latency_ms,
                "error_rate": error_rate,
                "service": event.payload.get("service"),
            },
        )

    @staticmethod
    def severity_for(observation: Observation, event_type: EventType) -> Severity:
        if event_type == EventType.BUDGET_WARNING:
            pct = float(observation.metrics.get("consumed_pct", 0))
            if pct >= 1.0:
                return Severity.P1
            if pct >= 0.9:
                return Severity.P2
        return Severity.P3
