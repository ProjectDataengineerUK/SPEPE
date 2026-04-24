from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sentinel.events.event_types import EventType, SentinelEvent, Severity


@dataclass
class Observation:
    detected: bool
    reason: str
    metrics: dict[str, Any]


class MLOpsWatcher:
    """Monitors drift JS divergence, Brier score and canary degradation."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.js_threshold = self.config.get("js_threshold", 0.10)
        self.brier_threshold = self.config.get("brier_threshold", 0.25)
        self.canary_relative_degradation = self.config.get(
            "canary_relative_degradation", 0.10
        )

    def observe(self, event: SentinelEvent) -> Observation:
        if event.type == EventType.DRIFT_DETECTED:
            return self._inspect_drift(event)
        if event.type == EventType.BIAS_ALERT:
            return self._inspect_bias(event)
        if event.type == EventType.CANARY_DEGRADATION:
            return self._inspect_canary(event)
        return Observation(False, "not_mlops_event", {})

    def _inspect_drift(self, event: SentinelEvent) -> Observation:
        js = float(event.payload.get("js_divergence", 0.0))
        return Observation(
            detected=js > self.js_threshold,
            reason=f"js_divergence={js:.3f} threshold={self.js_threshold}",
            metrics={
                "feature": event.payload.get("feature"),
                "js_divergence": js,
                "window": event.payload.get("window", "24h"),
            },
        )

    def _inspect_bias(self, event: SentinelEvent) -> Observation:
        gap_pp = float(event.payload.get("gap_pp", 0.0))
        return Observation(
            detected=gap_pp > 15.0,
            reason=f"bias_gap={gap_pp:.1f}pp",
            metrics={
                "dimension": event.payload.get("dimension"),
                "gap_pp": gap_pp,
            },
        )

    def _inspect_canary(self, event: SentinelEvent) -> Observation:
        champ = float(event.payload.get("champion_brier", 0.0))
        chall = float(event.payload.get("challenger_brier", 0.0))
        rel = (chall - champ) / max(champ, 1e-6)
        return Observation(
            detected=rel > self.canary_relative_degradation,
            reason=f"relative_degradation={rel:.3f}",
            metrics={
                "champion_brier": champ,
                "challenger_brier": chall,
                "relative_degradation": rel,
            },
        )

    @staticmethod
    def severity_for(observation: Observation, event_type: EventType) -> Severity:
        if event_type == EventType.CANARY_DEGRADATION:
            return Severity.P1
        if event_type == EventType.BIAS_ALERT:
            return Severity.P1
        return Severity.P2
