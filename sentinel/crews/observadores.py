from __future__ import annotations

from typing import Any

from sentinel.events.event_types import EventType, SentinelEvent
from sentinel.watchers import (
    DataOpsWatcher,
    InfraWatcher,
    MLOpsWatcher,
    SocialWatcher,
)


class ObservadoresCrew:
    """Crew 1: aggregates raw observations from all domain watchers."""

    DATAOPS_EVENTS = {
        EventType.DQ_VIOLATION,
        EventType.CONTRACT_BREACH,
        EventType.FRESHNESS_SLO_BREACH,
        EventType.PIPELINE_FAILURE,
    }
    MLOPS_EVENTS = {
        EventType.DRIFT_DETECTED,
        EventType.BIAS_ALERT,
        EventType.CANARY_DEGRADATION,
    }
    INFRA_EVENTS = {EventType.BUDGET_WARNING}
    SOCIAL_EVENTS = {EventType.SOCIAL_BURST}

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        watchers_cfg = self.config.get("watchers", {})
        self.dataops = DataOpsWatcher(watchers_cfg.get("dataops"))
        self.mlops = MLOpsWatcher(watchers_cfg.get("mlops"))
        self.infra = InfraWatcher(watchers_cfg.get("infra"))
        self.social = SocialWatcher(watchers_cfg.get("social"))

    def observe(self, event: SentinelEvent) -> dict[str, Any]:
        observations: dict[str, Any] = {}
        if event.type in self.DATAOPS_EVENTS:
            obs = self.dataops.observe(event)
            observations["dataops"] = self._pack(obs)
        if event.type in self.MLOPS_EVENTS:
            obs = self.mlops.observe(event)
            observations["mlops"] = self._pack(obs)
        if event.type in self.INFRA_EVENTS or event.type == EventType.PIPELINE_FAILURE:
            obs = self.infra.observe(event)
            observations["infra"] = self._pack(obs)
        if event.type in self.SOCIAL_EVENTS:
            obs = self.social.observe(event)
            observations["social"] = self._pack(obs)
        return {"event": event.to_dict(), "observations": observations}

    @staticmethod
    def _pack(obs) -> dict[str, Any]:
        return {"detected": obs.detected, "reason": obs.reason, "metrics": obs.metrics}
