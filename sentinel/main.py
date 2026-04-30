"""Sentinel Cloud Run entry point — receives Pub/Sub push events via FastAPI."""

from __future__ import annotations

import base64
import json
import logging
import os

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

from sentinel.orchestrator import SentinelOrchestrator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="SPEPE Sentinel", version="1.0.0")

_sentinel_enabled = os.environ.get("SENTINEL_ENABLED", "true").lower() == "true"
_config = {
    "project_id": os.environ.get("GCP_PROJECT_ID"),
    "kb_collection": os.environ.get("SENTINEL_KB_COLLECTION", "sentinel_kb"),
    "alert_topic": os.environ.get("SENTINEL_ALERT_TOPIC", "sentinel-alerts"),
}

_orchestrator: SentinelOrchestrator | None = None


def _get_orchestrator() -> SentinelOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SentinelOrchestrator(config=_config)
    return _orchestrator


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "sentinel_enabled": _sentinel_enabled}


@app.post("/sentinel/event")
async def receive_event(request: Request) -> Response:
    """Pub/Sub push subscription endpoint."""
    if not _sentinel_enabled:
        return Response(content="sentinel_disabled", status_code=200)

    body = await request.json()

    # Pub/Sub push envelope: {"message": {"data": "<base64>", ...}, "subscription": "..."}
    message = body.get("message", {})
    data_b64 = message.get("data", "")
    if not data_b64:
        raise HTTPException(status_code=400, detail="missing_message_data")

    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except Exception as exc:
        logger.warning("sentinel_decode_error: %s", exc)
        raise HTTPException(status_code=400, detail="invalid_message_data") from exc

    try:
        result = _get_orchestrator().handle_event(payload)
    except Exception as exc:
        logger.error("sentinel_orchestrator_error: %s", exc, exc_info=True)
        # Return 200 to avoid Pub/Sub retry loop for unrecoverable errors
        return Response(content=json.dumps({"handled": False, "error": str(exc)}), status_code=200)

    logger.info("sentinel_event_handled: %s", result.get("event_type", "unknown"))
    return Response(content=json.dumps(result), status_code=200)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("sentinel.main:app", host="0.0.0.0", port=port, log_level="info")
