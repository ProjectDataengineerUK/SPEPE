"""Cloud Trace integration for session/agent/token tracing."""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger("spepe.mlops.trace")

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-dev")


@dataclass
class SpanContext:
    session_id: str
    agent_name: str
    span_id: str = ""
    start_time: float = field(default_factory=time.time)
    token_count: int = 0
    cost_usd: float = 0.0
    attributes: dict = field(default_factory=dict)


def start_span(session_id: str, agent_name: str, operation: str = "") -> SpanContext:
    import uuid
    span = SpanContext(
        session_id=session_id,
        agent_name=agent_name,
        span_id=str(uuid.uuid4())[:8],
        attributes={"operation": operation},
    )
    logger.debug(f"Span started: {agent_name}/{operation} [{span.span_id}]")
    return span


def end_span(span: SpanContext, token_count: int = 0, cost_usd: float = 0.0) -> None:
    duration_ms = int((time.time() - span.start_time) * 1000)
    span.token_count = token_count
    span.cost_usd = cost_usd

    log_entry = {
        "session_id": span.session_id,
        "agent": span.agent_name,
        "span_id": span.span_id,
        "duration_ms": duration_ms,
        "token_count": token_count,
        "cost_usd": cost_usd,
        **span.attributes,
    }

    logger.info("TRACE %s", log_entry)
    _export_to_cloud_trace(span, duration_ms)


def _export_to_cloud_trace(span: SpanContext, duration_ms: int) -> None:
    try:
        from google.cloud import trace_v2
        from google.protobuf.timestamp_pb2 import Timestamp
        import time as time_module

        client = trace_v2.TraceServiceClient()
        project_name = f"projects/{GCP_PROJECT}"

        import uuid
        trace_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]

        now = time_module.time()
        start_ts = Timestamp()
        start_ts.FromSeconds(int(span.start_time))
        end_ts = Timestamp()
        end_ts.FromSeconds(int(now))

        spans = [trace_v2.Span(
            name=f"{project_name}/traces/{trace_id}/spans/{span_id}",
            span_id=span_id,
            display_name=trace_v2.TruncatableString(value=f"{span.agent_name}/{span.attributes.get('operation', '')}"),
            start_time=start_ts,
            end_time=end_ts,
            attributes=trace_v2.Span.Attributes(attribute_map={
                "session_id": trace_v2.AttributeValue(string_value=trace_v2.TruncatableString(value=span.session_id)),
                "agent": trace_v2.AttributeValue(string_value=trace_v2.TruncatableString(value=span.agent_name)),
                "token_count": trace_v2.AttributeValue(int_value=span.token_count),
            }),
        )]

        client.batch_write_spans(name=project_name, spans=spans)
    except Exception:
        pass


@contextmanager
def traced_operation(session_id: str, agent_name: str, operation: str = ""):
    span = start_span(session_id, agent_name, operation)
    try:
        yield span
    finally:
        end_span(span, token_count=span.token_count, cost_usd=span.cost_usd)
