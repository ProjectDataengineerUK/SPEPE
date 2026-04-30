from __future__ import annotations

import logging
from typing import Any

from sentinel.crews.analisadores import AnalisadoresCrew
from sentinel.crews.despachantes import DespachantesCrew
from sentinel.crews.interpretadores import InterpretadoresCrew
from sentinel.crews.observadores import ObservadoresCrew
from sentinel.events.event_types import EventType, SentinelEvent

logger = logging.getLogger(__name__)


class SentinelOrchestrator:
    """Main orchestrator: receives Pub/Sub event, routes to crews, dispatches."""

    def __init__(self, config: dict | None = None, action_handlers: dict | None = None):
        self.config = config or {}
        self.observadores = ObservadoresCrew(config=self.config)
        self.analisadores = AnalisadoresCrew(config=self.config)
        self.interpretadores = InterpretadoresCrew(config=self.config)
        self.despachantes = DespachantesCrew(
            config=self.config, action_handlers=action_handlers or {}
        )

    def handle_event(self, event: dict | SentinelEvent) -> dict[str, Any]:
        try:
            if isinstance(event, dict):
                event_obj = SentinelEvent.from_dict(event)
            else:
                event_obj = event
        except (ValueError, KeyError) as exc:
            logger.warning("invalid_event_payload: %s", exc)
            return {"handled": False, "reason": "unknown_event_type"}
        try:
            event_type = EventType(event_obj.type.value)
        except ValueError:
            logger.warning("unknown_event_type=%s", event_obj.type)
            return {"handled": False, "reason": "unknown_event_type"}
        observations = self.observadores.observe(event_obj)
        analysis = self.analisadores.analyze(observations)
        interpretation_bundle = self.interpretadores.interpret(analysis)
        dispatch_result = self.despachantes.dispatch(interpretation_bundle, event_type)
        interpretation = interpretation_bundle["interpretation"]
        outcome = "success" if dispatch_result.get("action", {}).get("executed") else "notified"
        self.interpretadores.record_outcome(
            event_type=event_type.value,
            correlations=interpretation_bundle.get("correlations", []),
            cause=interpretation.get("causa_raiz", "unknown"),
            action=interpretation.get("acao_recomendada", "unknown"),
            outcome=outcome,
        )
        return {
            "handled": True,
            "event_type": event_type.value,
            "interpretation": interpretation,
            "dispatch": dispatch_result,
        }
