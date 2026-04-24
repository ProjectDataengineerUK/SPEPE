from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Firestore-backed Knowledge Base for sentinel incidents/patterns/playbooks.

    Falls back to an in-memory store if Firestore client is unavailable — useful
    for tests and local runs.
    """

    INCIDENTS = "incidents"
    PATTERNS = "patterns"
    PLAYBOOKS = "playbooks"

    def __init__(self, project_id: str | None = None, collection: str = "sentinel_kb"):
        self.project_id = project_id
        self.collection = collection
        self._client = self._init_firestore()
        self._memory: dict[str, dict[str, dict]] = {
            self.INCIDENTS: {},
            self.PATTERNS: {},
            self.PLAYBOOKS: {},
        }

    def _init_firestore(self):
        try:
            from google.cloud import firestore

            return firestore.Client(project=self.project_id)
        except Exception as exc:
            logger.info("firestore_unavailable_using_memory: %s", exc)
            return None

    def _doc_ref(self, subcollection: str, doc_id: str):
        if self._client is None:
            return None
        return (
            self._client.collection(self.collection)
            .document(subcollection)
            .collection(subcollection)
            .document(doc_id)
        )

    def write_incident(self, incident: dict[str, Any]) -> str:
        incident_id = incident.get("incident_id") or str(uuid.uuid4())
        incident["incident_id"] = incident_id
        incident["timestamp"] = incident.get(
            "timestamp", datetime.now(timezone.utc).isoformat()
        )
        ref = self._doc_ref(self.INCIDENTS, incident_id)
        if ref is not None:
            ref.set(incident)
        else:
            self._memory[self.INCIDENTS][incident_id] = incident
        return incident_id

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        ref = self._doc_ref(self.INCIDENTS, incident_id)
        if ref is not None:
            snap = ref.get()
            return snap.to_dict() if snap.exists else None
        return self._memory[self.INCIDENTS].get(incident_id)

    def find_patterns(
        self, signature: dict[str, Any], limit: int = 5
    ) -> list[dict[str, Any]]:
        event_type = signature.get("event_type")
        if self._client is None:
            return [
                p
                for p in self._memory[self.PATTERNS].values()
                if p.get("trigger_signature", {}).get("event_type") == event_type
            ][:limit]
        col = (
            self._client.collection(self.collection)
            .document(self.PATTERNS)
            .collection(self.PATTERNS)
        )
        query = col.where("trigger_signature.event_type", "==", event_type).limit(limit)
        return [doc.to_dict() for doc in query.stream()]

    def upsert_pattern(self, pattern: dict[str, Any]) -> str:
        pattern_id = pattern.get("pattern_id") or str(uuid.uuid4())
        pattern["pattern_id"] = pattern_id
        ref = self._doc_ref(self.PATTERNS, pattern_id)
        if ref is not None:
            ref.set(pattern, merge=True)
        else:
            existing = self._memory[self.PATTERNS].get(pattern_id, {})
            existing.update(pattern)
            self._memory[self.PATTERNS][pattern_id] = existing
        return pattern_id

    def get_playbook(self, event_type: str) -> dict[str, Any] | None:
        ref = self._doc_ref(self.PLAYBOOKS, event_type)
        if ref is not None:
            snap = ref.get()
            return snap.to_dict() if snap.exists else None
        return self._memory[self.PLAYBOOKS].get(event_type)

    def list_recent_incidents(
        self, event_type: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        if self._client is None:
            items = list(self._memory[self.INCIDENTS].values())
        else:
            col = (
                self._client.collection(self.collection)
                .document(self.INCIDENTS)
                .collection(self.INCIDENTS)
            )
            query = col.order_by("timestamp", direction="DESCENDING").limit(limit * 3)
            items = [doc.to_dict() for doc in query.stream()]
        if event_type:
            items = [i for i in items if i.get("event_type") == event_type]
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return items[:limit]
