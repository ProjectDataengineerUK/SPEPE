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


class SocialWatcher:
    """Detects social volume bursts in fato_social: volume > mu + 3*sigma (1h window)."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.z_threshold = self.config.get("z_threshold", 3.0)
        self.window = self.config.get("window", "1h")

    def observe(self, event: SentinelEvent) -> Observation:
        if event.type != EventType.SOCIAL_BURST:
            return Observation(False, "not_social_event", {})
        payload = event.payload
        volume = float(payload.get("volume_mencoes", 0))
        mu = float(payload.get("mu", 0))
        sigma = float(payload.get("sigma", 1))
        z = (volume - mu) / max(sigma, 1e-6)
        return Observation(
            detected=z > self.z_threshold,
            reason=f"z_score={z:.2f} volume={volume:.0f}",
            metrics={
                "volume_mencoes": volume,
                "mu": mu,
                "sigma": sigma,
                "z": z,
                "sg_uf": payload.get("sg_uf"),
                "window": self.window,
            },
        )

    @staticmethod
    def severity_for(observation: Observation, event_type: EventType) -> Severity:
        z = float(observation.metrics.get("z", 0))
        if z > 5.0:
            return Severity.P1
        if z > 3.0:
            return Severity.P2
        return Severity.P3
