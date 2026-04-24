from __future__ import annotations

from typing import Any

from sentinel.events.event_types import SentinelEvent
from sentinel.genai_interpreter import GenAIInterpreter
from sentinel.kb.context_builder import ContextBuilder
from sentinel.kb.kb_updater import KBUpdater
from sentinel.kb.knowledge_base import KnowledgeBase


class InterpretadoresCrew:
    """Crew 3: ContextBuilder + GenAI Interpreter + KB Updater."""

    def __init__(self, config: dict | None = None, kb: KnowledgeBase | None = None):
        self.config = config or {}
        self.kb = kb or KnowledgeBase(project_id=self.config.get("project_id"))
        self.context_builder = ContextBuilder(self.kb)
        self.interpreter = GenAIInterpreter(
            model=self.config.get(
                "genai_interpreter_model", "claude-sonnet-4-6"
            ),
            api_key=self.config.get("anthropic_api_key"),
        )
        self.kb_updater = KBUpdater(self.kb)

    def interpret(self, analysis: dict[str, Any]) -> dict[str, Any]:
        event = SentinelEvent.from_dict(analysis["event"])
        context = self.context_builder.build(
            event=event,
            patterns=analysis.get("patterns", []),
            correlations=analysis.get("correlations", []),
            recent_metrics=analysis.get("anomaly", {}) or {},
        )
        interpretation = self.interpreter.interpret(context)
        return {
            "event": analysis["event"],
            "interpretation": interpretation,
            "correlations": analysis.get("correlations", []),
        }

    def record_outcome(
        self,
        event_type: str,
        correlations: list[str],
        cause: str,
        action: str,
        outcome: str,
    ) -> str:
        return self.kb_updater.record_incident(
            event_type=event_type,
            correlations=correlations,
            cause=cause,
            action=action,
            outcome=outcome,
        )
