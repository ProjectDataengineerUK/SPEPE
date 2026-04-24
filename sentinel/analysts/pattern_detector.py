from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sentinel.events.event_types import SentinelEvent

logger = logging.getLogger(__name__)


@dataclass
class PatternMatch:
    pattern_id: str
    probable_cause: str
    recommended_action: str
    confidence: float
    occurrences: int


class PatternDetector:
    """Detects historical incident patterns by comparing trigger signatures in KB."""

    def __init__(self, kb_client=None, min_confidence: float = 0.60):
        self.kb = kb_client
        self.min_confidence = min_confidence

    def detect(self, event: SentinelEvent) -> list[PatternMatch]:
        if self.kb is None:
            logger.debug("no_kb_client_configured")
            return []
        signature = self._build_signature(event)
        patterns = self.kb.find_patterns(signature=signature, limit=5) or []
        matches: list[PatternMatch] = []
        for pat in patterns:
            conf = float(pat.get("confidence", 0.0))
            if conf < self.min_confidence:
                continue
            matches.append(
                PatternMatch(
                    pattern_id=pat.get("pattern_id", "unknown"),
                    probable_cause=pat.get("probable_cause", ""),
                    recommended_action=pat.get("recommended_action", ""),
                    confidence=conf,
                    occurrences=int(pat.get("occurrences", 1)),
                )
            )
        return matches

    def _build_signature(self, event: SentinelEvent) -> dict[str, Any]:
        return {
            "event_type": event.type.value,
            "source": event.source,
            "features": list(event.payload.keys()),
        }
