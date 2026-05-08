"""Integration tests for /admin/api/sentinel/stream (SSE).

Tests drive the async generator directly to avoid TestClient hanging on the
30s wait_for inside the SSE event loop.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest


# ---------------------------------------------------------------------------
# Test setup — force stub mode (no GCP, no auth)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_stub_mode():
    os.environ["USE_BIGQUERY"] = "false"
    from config.settings import settings as _s

    original = _s.gcp_project_id
    _s.gcp_project_id = ""
    yield
    _s.gcp_project_id = original


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# _format_sse — pure function
# ---------------------------------------------------------------------------


def test_format_sse_emits_event_and_data_lines() -> None:
    from ui.dashboard_api import _format_sse

    out = _format_sse("foo", {"a": 1})
    assert out.startswith("event: foo\n")
    assert "data: " in out
    assert out.endswith("\n\n")
    payload_line = [line for line in out.splitlines() if line.startswith("data: ")][0]
    assert json.loads(payload_line[len("data: ") :]) == {"a": 1}


# ---------------------------------------------------------------------------
# Endpoint smoke — content type + status
# ---------------------------------------------------------------------------


def test_sse_endpoint_returns_event_stream_content_type() -> None:
    """The route returns a StreamingResponse with text/event-stream."""
    from ui.dashboard_api import admin_sentinel_stream

    async def _go():
        resp = await admin_sentinel_stream()
        ct = resp.media_type or resp.headers.get("content-type", "")
        # Drain a single frame from the generator and close it.
        gen = resp.body_iterator
        try:
            first = await gen.__anext__()
        finally:
            await gen.aclose()
        return ct, first

    ct, first = _run(_go())
    assert ct.startswith("text/event-stream")
    assert "event:" in first


# ---------------------------------------------------------------------------
# Snapshot is the first frame
# ---------------------------------------------------------------------------


def test_sse_snapshot_event_emitted() -> None:
    """The first SSE frame must be an `event: snapshot` with stub source."""
    from ui.dashboard_api import admin_sentinel_stream

    async def _go():
        resp = await admin_sentinel_stream()
        gen = resp.body_iterator
        try:
            first = await gen.__anext__()
        finally:
            await gen.aclose()
        return first

    first = _run(_go())
    assert first.startswith("event: snapshot")
    data_line = [line for line in first.splitlines() if line.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: ") :])
    assert payload.get("source") == "stub"
    assert "maturity" in payload
    assert payload["maturity"] == {"dataops": 0, "mlops": 0, "llmops": 0}


# ---------------------------------------------------------------------------
# Fallback when USE_BIGQUERY=false (AT-004)
# ---------------------------------------------------------------------------


def test_fallback_stub_when_use_bigquery_false() -> None:
    """AT-004 — USE_BIGQUERY=false → snapshot is a stub, no error."""
    os.environ["USE_BIGQUERY"] = "false"
    from ui.dashboard_api import _build_full_snapshot

    snap = _run(_build_full_snapshot())
    assert snap["source"] == "stub"
    assert snap["maturity"] == {"dataops": 0, "mlops": 0, "llmops": 0}
    assert snap["jobs"] == []


# ---------------------------------------------------------------------------
# Heartbeat behavior (timeout-based)
# ---------------------------------------------------------------------------


def test_sse_heartbeat_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no events arrive within the wait timeout, a `heartbeat` is emitted."""
    import ui.dashboard_api as api

    api._sentinel_subscribers.clear()

    original_wait_for = asyncio.wait_for

    async def fake_wait_for(coro, timeout):  # noqa: ARG001
        try:
            coro.close()
        except Exception:
            pass
        raise asyncio.TimeoutError()

    async def _go():
        sse = await api.admin_sentinel_stream()
        gen = sse.body_iterator
        # Patch only after grabbing the snapshot
        first = await gen.__anext__()
        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        try:
            second = await gen.__anext__()
        finally:
            monkeypatch.setattr(asyncio, "wait_for", original_wait_for)
            await gen.aclose()
        return first, second

    first, second = _run(_go())
    assert "event: snapshot" in first
    assert "event: heartbeat" in second


# ---------------------------------------------------------------------------
# Broadcast fan-out
# ---------------------------------------------------------------------------


def test_broadcast_delivers_to_each_subscriber() -> None:
    import ui.dashboard_api as api

    async def _go():
        api._sentinel_subscribers.clear()
        q1: asyncio.Queue = asyncio.Queue(maxsize=4)
        q2: asyncio.Queue = asyncio.Queue(maxsize=4)
        api._sentinel_subscribers.extend([q1, q2])
        await api._sentinel_broadcast("job_status_changed", {"job": "X"})
        return q1.get_nowait(), q2.get_nowait()

    a, b = _run(_go())
    assert a == ("job_status_changed", {"job": "X"})
    assert b == ("job_status_changed", {"job": "X"})


def test_broadcast_drops_full_queues() -> None:
    """A QueueFull subscriber must be removed from the subscriber list."""
    import ui.dashboard_api as api

    async def _go():
        api._sentinel_subscribers.clear()
        q_full: asyncio.Queue = asyncio.Queue(maxsize=1)
        q_full.put_nowait(("first", {}))
        api._sentinel_subscribers.append(q_full)
        await api._sentinel_broadcast("evt", {"k": 1})
        return list(api._sentinel_subscribers)

    remaining = _run(_go())
    assert remaining == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
