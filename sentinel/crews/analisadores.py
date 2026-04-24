from __future__ import annotations

from typing import Any

from sentinel.analysts import AnomalyDetector, PatternDetector
from sentinel.events.event_types import SentinelEvent
from sentinel.kb.knowledge_base import KnowledgeBase


class AnalisadoresCrew:
    """Crew 2: pattern detection + anomaly detection + cross-domain correlation."""

    def __init__(self, config: dict | None = None, kb: KnowledgeBase | None = None):
        self.config = config or {}
        self.kb = kb or KnowledgeBase(project_id=self.config.get("project_id"))
        self.pattern_detector = PatternDetector(kb_client=self.kb)
        self.anomaly_detector = AnomalyDetector(
            z_threshold=self.config.get("z_threshold", 3.0)
        )

    def analyze(self, observations_bundle: dict[str, Any]) -> dict[str, Any]:
        event_dict = observations_bundle["event"]
        event = SentinelEvent.from_dict(event_dict)
        obs = observations_bundle.get("observations", {})
        patterns = self.pattern_detector.detect(event)
        pattern_dicts = [p.__dict__ for p in patterns]
        correlations = [
            f"{domain}: {data['reason']}"
            for domain, data in obs.items()
            if data.get("detected")
        ]
        primary_metric, primary_history = self._primary_metric(obs)
        anomaly = None
        if primary_metric is not None:
            anomaly = self.anomaly_detector.detect(
                current_value=primary_metric,
                history=primary_history,
                correlated_observations=obs,
            )
        return {
            "event": event_dict,
            "observations": obs,
            "patterns": pattern_dicts,
            "correlations": correlations,
            "anomaly": anomaly.__dict__ if anomaly else None,
        }

    def _primary_metric(
        self, obs: dict[str, Any]
    ) -> tuple[float | None, list[float]]:
        for _domain, data in obs.items():
            metrics = data.get("metrics", {})
            for key in (
                "js_divergence",
                "pass_rate",
                "volume_mencoes",
                "p99_latency_ms",
                "delay_hours",
                "relative_degradation",
                "gap_pp",
                "consumed_pct",
            ):
                if key in metrics:
                    return float(metrics[key]), []
        return None, []
