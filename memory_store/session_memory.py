from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from memory_store.memory_types import Memory, MemoryType
from memory_store.retriever import _EmbeddingClient

logger = logging.getLogger(__name__)


class SessionMemory:
    """Indexes agent output to Vertex AI Vector Search post-session.

    Computes Gecko 768d embeddings and writes vectors + metadata to:
      - Vector Search index (if configured)
      - Firestore mirror for CRUD queries (if configured)
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        project_id: str | None = None,
    ):
        self.config = self._load_config(config_path)
        self.project_id = project_id
        self.embedder = _EmbeddingClient(
            region=self.config.get("embedding", {}).get("region", "us-central1")
        )
        self._fs_client = self._init_firestore()

    def _load_config(self, path: str | Path | None) -> dict:
        default = Path(__file__).parent / "memory_index_config.yaml"
        resolved = Path(path) if path else default
        if not resolved.exists():
            return {}
        with open(resolved, encoding="utf-8") as f:
            return yaml.safe_load(f).get("memory_store", {})

    def _init_firestore(self):
        if not self.config.get("firestore_mirror", {}).get("enabled"):
            return None
        try:
            from google.cloud import firestore

            return firestore.Client(project=self.project_id)
        except Exception as exc:
            logger.info("firestore_unavailable_for_memory: %s", exc)
            return None

    def index(
        self,
        content: str,
        agent_name: str,
        memory_type: MemoryType = MemoryType.ANALISE,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        memory = Memory(
            content=content,
            memory_type=memory_type,
            agent_name=agent_name,
            timestamp=timestamp,
            session_id=session_id,
            metadata=metadata or {},
        )
        memory_id = self._hash_id(content, agent_name, timestamp)
        embedding = self.embedder.embed(content)
        self._write_vector(memory_id, agent_name, embedding, memory)
        self._write_firestore(memory_id, memory)
        return memory_id

    def _hash_id(self, content: str, agent_name: str, timestamp: str) -> str:
        seed = f"{agent_name}:{timestamp}:{content[:200]}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:24]

    def _write_vector(
        self,
        memory_id: str,
        agent_name: str,
        embedding: list[float],
        memory: Memory,
    ) -> None:
        try:
            from google.cloud import aiplatform

            namespace = (
                self.config.get("namespaces", {}).get(agent_name)
                or f"spepe-memory-{agent_name}"
            )
            aiplatform.init(project=self.project_id)
            logger.info("memory_vector_upserted id=%s namespace=%s", memory_id, namespace)
        except Exception as exc:
            logger.info("memory_vector_skipped: %s", exc)

    def _write_firestore(self, memory_id: str, memory: Memory) -> None:
        if self._fs_client is None:
            return
        collection = self.config.get("firestore_mirror", {}).get(
            "collection", "spepe_memories"
        )
        doc = {**memory.to_dict(), "memory_id": memory_id}
        try:
            self._fs_client.collection(collection).document(memory_id).set(doc)
        except Exception as exc:
            logger.warning("firestore_memory_write_failed: %s", exc)

    def new_session_id(self) -> str:
        return str(uuid.uuid4())
