from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from memory_store.memory_types import Memory, MemoryType

logger = logging.getLogger(__name__)


@dataclass
class _EmbeddingClient:
    model: str = "textembedding-gecko@003"
    region: str = "us-central1"

    def embed(self, text: str) -> list[float]:
        try:
            from vertexai.language_models import TextEmbeddingModel

            model = TextEmbeddingModel.from_pretrained(self.model)
            response = model.get_embeddings([text])
            return list(response[0].values)
        except Exception as exc:
            logger.info("vertex_embedding_unavailable_fallback: %s", exc)
            return self._fallback(text)

    @staticmethod
    def _fallback(text: str) -> list[float]:
        import hashlib

        import numpy as np

        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = np.random.default_rng(seed)
        return rng.standard_normal(768).tolist()


def retrieve_relevant_memories(
    query: str,
    agent_name: str,
    index_endpoint_name: str | None = None,
    k: int = 5,
    min_similarity: float = 0.75,
    project_id: str | None = None,
    region: str = "us-central1",
) -> list[Memory]:
    """Retrieves up to K memories from Vertex Vector Search for `agent_name`.

    Gracefully degrades to an empty list if Vector Search is not configured.
    """
    if not index_endpoint_name:
        logger.debug("no_vector_search_endpoint_configured")
        return []

    embedder = _EmbeddingClient(region=region)
    query_vec = embedder.embed(query)

    try:
        from google.cloud import aiplatform

        aiplatform.init(project=project_id, location=region)
        endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=index_endpoint_name)
        response = endpoint.find_neighbors(
            deployed_index_id=agent_name,
            queries=[query_vec],
            num_neighbors=k,
        )
        results: list[Memory] = []
        for neighbor_list in response:
            for n in neighbor_list:
                similarity = 1.0 - float(n.distance or 0.0)
                if similarity < min_similarity:
                    continue
                meta: dict[str, Any] = getattr(n, "restricts", None) or {}
                results.append(
                    Memory(
                        content=str(meta.get("content", "")),
                        memory_type=MemoryType(meta.get("memory_type", MemoryType.ANALISE.value)),
                        agent_name=agent_name,
                        timestamp=str(meta.get("timestamp", "")),
                        similarity=similarity,
                        metadata=dict(meta),
                    )
                )
        return results
    except Exception as exc:
        logger.info("vector_search_unavailable: %s", exc)
        return []
