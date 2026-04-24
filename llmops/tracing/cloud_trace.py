from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass
class Span:
    name: str
    span_id: str
    parent_id: str | None
    start_ms: float
    end_ms: float | None = None
    attributes: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.end_ms or time.monotonic() * 1000) - self.start_ms


class CloudTracer:
    """Cloud Trace spans per session/agent. Falls back to structured logging."""

    def __init__(self, project_id: str | None = None):
        self.project_id = project_id
        self._tracer = self._init_tracer()
        self._active: list[Span] = []

    def _init_tracer(self):
        try:
            from opentelemetry import trace

            return trace.get_tracer("spepe.llmops")
        except Exception as exc:
            logger.info("opentelemetry_unavailable: %s", exc)
            return None

    @contextmanager
    def span(self, name: str, **attributes) -> Iterator[Span]:
        span = Span(
            name=name,
            span_id=uuid.uuid4().hex[:16],
            parent_id=self._active[-1].span_id if self._active else None,
            start_ms=time.monotonic() * 1000,
            attributes=attributes,
        )
        self._active.append(span)
        try:
            yield span
        finally:
            span.end_ms = time.monotonic() * 1000
            self._active.pop()
            logger.info(
                "span name=%s duration_ms=%.2f attrs=%s",
                span.name,
                span.duration_ms,
                span.attributes,
            )
