from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sentinel.events.event_types import SentinelEvent
from sentinel.kb.knowledge_base import KnowledgeBase


@dataclass
class SentinelContext:
    event: dict[str, Any]
    history: list[dict[str, Any]] = field(default_factory=list)
    patterns: list[dict[str, Any]] = field(default_factory=list)
    correlations: list[str] = field(default_factory=list)
    recent_metrics: dict[str, Any] = field(default_factory=dict)

    def to_prompt(self) -> str:
        lines = [
            "# Sentinel Context",
            f"Event type: {self.event.get('type')}",
            f"Source: {self.event.get('source')}",
            f"Severity: {self.event.get('severity')}",
            f"Timestamp: {self.event.get('timestamp')}",
            "",
            "## Current payload",
            str(self.event.get("payload", {})),
            "",
            f"## KB history ({len(self.history)} similar incidents)",
        ]
        for item in self.history[:5]:
            lines.append(
                f"- {item.get('timestamp', '?')} "
                f"cause={item.get('cause', '?')} action={item.get('action', '?')} "
                f"outcome={item.get('outcome', '?')}"
            )
        lines.extend(
            [
                "",
                f"## Patterns matched ({len(self.patterns)})",
            ]
        )
        for p in self.patterns:
            lines.append(
                f"- {p.get('pattern_id')} cause={p.get('probable_cause')} "
                f"action={p.get('recommended_action')} conf={p.get('confidence')}"
            )
        if self.correlations:
            lines.append("")
            lines.append("## Correlated signals")
            for c in self.correlations:
                lines.append(f"- {c}")
        return "\n".join(lines)


class ContextBuilder:
    """Builds context: KB history + recent data + current event + correlations."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def build(
        self,
        event: SentinelEvent,
        patterns: list[dict] | None = None,
        correlations: list[str] | None = None,
        recent_metrics: dict | None = None,
    ) -> SentinelContext:
        history = self.kb.list_recent_incidents(event_type=event.type.value, limit=5)
        return SentinelContext(
            event=event.to_dict(),
            history=history,
            patterns=patterns or [],
            correlations=correlations or [],
            recent_metrics=recent_metrics or {},
        )
