from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class MemoryManager:
    """TTL enforcement, near-duplicate dedup and periodic compaction."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        project_id: str | None = None,
    ):
        self.config = self._load_config(config_path)
        self.project_id = project_id
        lifecycle = self.config.get("lifecycle", {})
        self.ttl_days = int(lifecycle.get("ttl_days", 365))
        self.dedup_similarity = float(lifecycle.get("dedup_similarity", 0.98))
        self._fs_client = self._init_firestore()

    def _load_config(self, path: str | Path | None) -> dict:
        default = Path(__file__).parent / "memory_index_config.yaml"
        resolved = Path(path) if path else default
        if not resolved.exists():
            return {}
        with open(resolved, encoding="utf-8") as f:
            return yaml.safe_load(f).get("memory_store", {})

    def _init_firestore(self):
        try:
            from google.cloud import firestore

            return firestore.Client(project=self.project_id)
        except Exception as exc:
            logger.info("firestore_unavailable_for_manager: %s", exc)
            return None

    def enforce_ttl(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.ttl_days)
        cutoff_iso = cutoff.isoformat()
        if self._fs_client is None:
            logger.info("ttl_enforce_skipped_no_fs")
            return 0
        collection = self.config.get("firestore_mirror", {}).get(
            "collection", "spepe_memories"
        )
        deleted = 0
        try:
            docs = (
                self._fs_client.collection(collection)
                .where("timestamp", "<", cutoff_iso)
                .stream()
            )
            for doc in docs:
                doc.reference.delete()
                deleted += 1
        except Exception as exc:
            logger.warning("ttl_enforce_query_failed: %s", exc)
        logger.info("memory_ttl_enforced deleted=%d cutoff=%s", deleted, cutoff_iso)
        return deleted

    def dedup(self, memories: list[dict]) -> list[dict]:
        if len(memories) < 2:
            return memories
        try:
            import numpy as np
        except ImportError:
            return memories
        kept: list[dict] = []
        kept_vecs: list[list[float]] = []
        for m in memories:
            vec = m.get("embedding")
            if not vec:
                kept.append(m)
                continue
            v = np.asarray(vec, dtype=float)
            is_dup = False
            for kv in kept_vecs:
                kva = np.asarray(kv, dtype=float)
                denom = np.linalg.norm(v) * np.linalg.norm(kva)
                if denom == 0:
                    continue
                sim = float(np.dot(v, kva) / denom)
                if sim >= self.dedup_similarity:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(m)
                kept_vecs.append(vec)
        return kept

    def compact(self) -> dict[str, int]:
        deleted = self.enforce_ttl()
        return {"ttl_deleted": deleted}
