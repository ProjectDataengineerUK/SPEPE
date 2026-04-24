from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sentinel.kb.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class KBUpdater:
    """Persists post-resolution context: cause + action + outcome for learning."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def record_incident(
        self,
        event_type: str,
        correlations: list[str],
        cause: str,
        action: str,
        outcome: str,
        resolved_by: str = "sentinel",
    ) -> str:
        incident = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlations": correlations,
            "cause": cause,
            "action": action,
            "outcome": outcome,
            "resolved_by": resolved_by,
        }
        incident_id = self.kb.write_incident(incident)
        self._reinforce_pattern(event_type, cause, action, outcome)
        return incident_id

    def _reinforce_pattern(
        self, event_type: str, cause: str, action: str, outcome: str
    ) -> None:
        pattern_id = f"{event_type}:{hash(cause) & 0xFFFFFF:06x}"
        existing = self.kb.find_patterns({"event_type": event_type}, limit=50)
        match = next(
            (p for p in existing if p.get("pattern_id") == pattern_id),
            None,
        )
        if match is None:
            pattern: dict[str, Any] = {
                "pattern_id": pattern_id,
                "trigger_signature": {"event_type": event_type},
                "probable_cause": cause,
                "recommended_action": action,
                "confidence": 0.60 if outcome == "success" else 0.30,
                "occurrences": 1,
            }
        else:
            occ = int(match.get("occurrences", 1)) + 1
            prev_conf = float(match.get("confidence", 0.5))
            if outcome == "success":
                new_conf = min(0.98, prev_conf + (1 - prev_conf) * 0.1)
            else:
                new_conf = max(0.05, prev_conf * 0.85)
            pattern = {
                "pattern_id": pattern_id,
                "trigger_signature": {"event_type": event_type},
                "probable_cause": cause,
                "recommended_action": action,
                "confidence": new_conf,
                "occurrences": occ,
            }
        self.kb.upsert_pattern(pattern)
