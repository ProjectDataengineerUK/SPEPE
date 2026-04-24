"""Rate limiting hook — per-user and per-session request throttling."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

logger = logging.getLogger("spepe.hooks.rate_limit")

_request_log: dict[str, list[float]] = defaultdict(list)
_session_request_counts: dict[str, int] = defaultdict(int)

MAX_REQUESTS_PER_MINUTE = 60
MAX_REQUESTS_PER_SESSION = 200
WINDOW_SECONDS = 60


def rate_limit_hook(session_id: str) -> str | None:
    """Check rate limits. Returns block message or None."""
    now = time.time()

    window_start = now - WINDOW_SECONDS
    _request_log[session_id] = [t for t in _request_log[session_id] if t > window_start]
    _request_log[session_id].append(now)

    requests_in_window = len(_request_log[session_id])
    if requests_in_window > MAX_REQUESTS_PER_MINUTE:
        msg = (
            f"RATE LIMIT: {requests_in_window} requests em 60s (max: {MAX_REQUESTS_PER_MINUTE}). "
            f"Aguarde antes de continuar."
        )
        logger.warning(f"Rate limit atingido: session={session_id} requests/min={requests_in_window}")
        return msg

    _session_request_counts[session_id] += 1
    total = _session_request_counts[session_id]
    if total > MAX_REQUESTS_PER_SESSION:
        msg = (
            f"SESSION LIMIT: {total} requests nesta sessão (max: {MAX_REQUESTS_PER_SESSION}). "
            "Inicie uma nova sessão para continuar."
        )
        logger.warning(f"Session limit atingido: session={session_id} total={total}")
        return msg

    if total > MAX_REQUESTS_PER_SESSION * 0.9:
        logger.info(f"Session {session_id}: {total}/{MAX_REQUESTS_PER_SESSION} requests")

    return None


def get_session_stats(session_id: str) -> dict:
    now = time.time()
    window_start = now - WINDOW_SECONDS
    recent = [t for t in _request_log.get(session_id, []) if t > window_start]
    return {
        "session_id": session_id,
        "requests_last_minute": len(recent),
        "total_requests": _session_request_counts.get(session_id, 0),
        "rate_limit_per_minute": MAX_REQUESTS_PER_MINUTE,
        "session_limit": MAX_REQUESTS_PER_SESSION,
    }
