from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CacheInvalidator:
    """Invalidates semantic cache entries after Gold tables are refreshed."""

    def __init__(self, redis_client=None):
        self._r = redis_client

    def invalidate_all(self) -> int:
        if self._r is None:
            logger.info("cache_invalidator_no_redis")
            return 0
        deleted = 0
        try:
            for key in self._r.scan_iter("cache:*"):
                self._r.delete(key)
                deleted += 1
        except Exception as exc:
            logger.warning("cache_invalidation_failed: %s", exc)
        logger.info("cache_invalidated entries=%d", deleted)
        return deleted

    def invalidate_for_table(self, table: str) -> int:
        if self._r is None:
            return 0
        deleted = 0
        pattern = f"cache:*:{table}:*"
        try:
            for key in self._r.scan_iter(pattern):
                self._r.delete(key)
                deleted += 1
        except Exception as exc:
            logger.warning("table_cache_invalidation_failed: %s", exc)
        return deleted

    def on_gold_refresh(self, tables: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for t in tables:
            result[t] = self.invalidate_for_table(t)
        return result
