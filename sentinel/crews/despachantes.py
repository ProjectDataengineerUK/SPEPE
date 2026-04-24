from __future__ import annotations

from typing import Any

from sentinel.dispatch import ActionExecutor, Dispatcher, Reporter
from sentinel.events.event_types import EventType


class DespachantesCrew:
    """Crew 4: Reporter + Dispatcher + ActionExecutor."""

    def __init__(
        self,
        config: dict | None = None,
        action_handlers: dict | None = None,
    ):
        self.config = config or {}
        self.reporter = Reporter()
        self.dispatcher = Dispatcher(
            slack_webhook=self.config.get("slack_webhook"),
            project_id=self.config.get("project_id"),
            alerts_topic=self.config.get("alerts_topic", "sentinel-alerts"),
        )
        self.executor = ActionExecutor(
            auto_actions_enabled=self.config.get("auto_actions_enabled", {}),
            action_handlers=action_handlers or {},
        )

    def dispatch(
        self, interpretation_bundle: dict[str, Any], event_type: EventType
    ) -> dict[str, Any]:
        event = interpretation_bundle["event"]
        interpretation = interpretation_bundle["interpretation"]
        action_result: dict[str, Any] = {"executed": False}
        if not interpretation.get("requer_humano", True):
            action_name = self._map_action(
                event_type.value, interpretation.get("acao_recomendada", "")
            )
            if action_name:
                action_result = self.executor.execute(
                    action=action_name,
                    event_type=event_type.value,
                    params={"event": event, "interpretation": interpretation},
                )
        report = self.reporter.format(
            event=event,
            interpretation=interpretation,
            action_taken=str(action_result) if action_result.get("executed") else None,
        )
        dispatch_result = self.dispatcher.dispatch(report, event)
        return {
            "report": report,
            "dispatched": dispatch_result,
            "action": action_result,
        }

    def _map_action(self, event_type: str, suggested: str) -> str | None:
        suggested_lower = suggested.lower()
        if "rollback" in suggested_lower:
            return "rollback"
        if "retrain" in suggested_lower:
            return "retrain"
        if "retry" in suggested_lower or "reattempt" in suggested_lower:
            return "pipeline_retry"
        if "scale" in suggested_lower:
            return "scale_job"
        if "block" in suggested_lower:
            return "block_pipeline"
        return None
