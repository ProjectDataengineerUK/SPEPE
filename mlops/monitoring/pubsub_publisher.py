"""Pub/Sub publisher for drift-detected events."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TOPIC_ID = "spepe-drift-detected"
_publisher = None


def _get_publisher():
    global _publisher
    if _publisher is None:
        try:
            from google.cloud import pubsub_v1

            _publisher = pubsub_v1.PublisherClient()
        except ImportError:
            logger.warning("google-cloud-pubsub not installed — using local fallback")
    return _publisher


def publish_drift_event(
    feature: str,
    js_score: float,
    threshold: float,
    project_id: str | None = None,
) -> str | None:
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    payload: dict[str, Any] = {
        "feature": feature,
        "js_divergence": js_score,
        "threshold": threshold,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
    }
    data = json.dumps(payload).encode("utf-8")

    publisher = _get_publisher()
    if publisher is None:
        _fallback_log(payload)
        return None

    try:
        topic_path = publisher.topic_path(project_id, TOPIC_ID)
        future = publisher.publish(topic_path, data=data)
        message_id = future.result(timeout=10)
        logger.info(
            "Drift event published: %s (score=%.4f) → msg_id=%s",
            feature,
            js_score,
            message_id,
        )
        return message_id
    except Exception as exc:
        logger.error("Failed to publish drift event: %s", exc)
        _fallback_log(payload)
        return None


def _fallback_log(payload: dict[str, Any]) -> None:
    import pathlib

    log_path = pathlib.Path("output/logs/drift_events.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(payload) + "\n")
    logger.info("Drift event written to fallback: %s", log_path)
