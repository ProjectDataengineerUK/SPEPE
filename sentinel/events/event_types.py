from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EventType(str, Enum):
    DQ_VIOLATION = "dq_violation"
    CONTRACT_BREACH = "contract_breach"
    DRIFT_DETECTED = "drift_detected"
    BIAS_ALERT = "bias_alert"
    FRESHNESS_SLO_BREACH = "freshness_slo_breach"
    BUDGET_WARNING = "budget_warning"
    SOCIAL_BURST = "social_burst"
    PIPELINE_FAILURE = "pipeline_failure"
    CANARY_DEGRADATION = "canary_degradation"


class Severity(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


@dataclass
class SentinelEvent:
    type: EventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    severity: Severity = Severity.P2
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "source": self.source,
            "payload": self.payload,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SentinelEvent":
        return cls(
            type=EventType(data["type"]),
            source=data.get("source", "unknown"),
            payload=data.get("payload", {}),
            severity=Severity(data.get("severity", "P2")),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            correlation_id=data.get("correlation_id"),
        )
