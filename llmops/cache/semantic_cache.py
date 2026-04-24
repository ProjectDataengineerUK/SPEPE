from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)


class SemanticCache:
    """Redis-backed semantic cache with embedding similarity >= 0.95.

    Falls back to an in-process dict when Redis is unavailable.
    Tracks hit_rate per agent for emission to Cloud Monitoring.
    """

    EMB_PREFIX = "cache:emb:"
    RESP_PREFIX = "cache:resp:"

    def __init__(
        self,
        redis_url: str | None = None,
        similarity_threshold: float = 0.95,
        ttl_seconds: int = 86400,
        embedder: Callable[[str], np.ndarray] | None = None,
    ):
        self.threshold = similarity_threshold
        self.ttl = ttl_seconds
        self.embedder = embedder or self._default_embedder
        self._r = self._connect(redis_url)
        self._local: dict[str, tuple[np.ndarray, str]] = {}
        self._stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"hit": 0, "miss": 0}
        )

    def _connect(self, redis_url: str | None):
        if not redis_url:
            return None
        try:
            import redis as redis_lib

            return redis_lib.from_url(redis_url)
        except Exception as exc:
            logger.info("redis_unavailable_using_local: %s", exc)
            return None

    @staticmethod
    def _default_embedder(text: str) -> np.ndarray:
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = np.random.default_rng(seed)
        return rng.standard_normal(1536)

    def get_or_compute(
        self, query: str, compute_fn: Callable[[str], str], agent: str = "default"
    ) -> tuple[str, bool]:
        q_emb = self.embedder(query)
        hit = self._lookup(q_emb)
        if hit is not None:
            self._stats[agent]["hit"] += 1
            return hit, True
        response = compute_fn(query)
        self._store(query, q_emb, response)
        self._stats[agent]["miss"] += 1
        return response, False

    def _lookup(self, q_emb: np.ndarray) -> str | None:
        if self._r is not None:
            try:
                for key in self._r.scan_iter(f"{self.EMB_PREFIX}*"):
                    raw = self._r.get(key)
                    if not raw:
                        continue
                    cached = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
                    if self._similarity(q_emb, cached) >= self.threshold:
                        resp_key = key.replace(
                            self.EMB_PREFIX.encode(), self.RESP_PREFIX.encode()
                        )
                        payload = self._r.get(resp_key)
                        if payload:
                            return payload.decode("utf-8")
            except Exception as exc:
                logger.warning("redis_lookup_failed: %s", exc)
                return None
            return None
        for _key_id, (cached, response) in self._local.items():
            if self._similarity(q_emb, cached) >= self.threshold:
                return response
        return None

    def _store(self, query: str, q_emb: np.ndarray, response: str) -> None:
        key_id = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        if self._r is not None:
            try:
                self._r.setex(
                    f"{self.EMB_PREFIX}{key_id}",
                    self.ttl,
                    q_emb.astype(np.float32).tobytes(),
                )
                self._r.setex(
                    f"{self.RESP_PREFIX}{key_id}", self.ttl, response.encode("utf-8")
                )
                return
            except Exception as exc:
                logger.warning("redis_store_failed: %s", exc)
        self._local[key_id] = (q_emb, response)

    @staticmethod
    def _similarity(a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)

    def hit_rate(self, agent: str) -> float:
        s = self._stats[agent]
        total = s["hit"] + s["miss"]
        return 0.0 if total == 0 else s["hit"] / total

    def stats(self) -> dict[str, dict[str, int]]:
        return {k: dict(v) for k, v in self._stats.items()}
