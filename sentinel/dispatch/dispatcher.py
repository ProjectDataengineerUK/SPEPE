from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Dispatcher:
    """Dispatches formatted alerts to Slack, Cloud Logging, Pub/Sub and dashboard."""

    def __init__(
        self,
        slack_webhook: str | None = None,
        project_id: str | None = None,
        alerts_topic: str = "sentinel-alerts",
    ):
        self.slack_webhook = slack_webhook
        self.project_id = project_id
        self.alerts_topic = alerts_topic

    def dispatch(
        self, markdown_report: str, payload: dict[str, Any]
    ) -> dict[str, bool]:
        result = {
            "slack": self._send_slack(markdown_report, payload),
            "cloud_logging": self._log_cloud(markdown_report, payload),
            "pubsub": self._publish_pubsub(payload),
        }
        return result

    def _send_slack(self, report: str, payload: dict) -> bool:
        if not self.slack_webhook:
            logger.debug("slack_webhook_not_configured")
            return False
        try:
            import requests

            requests.post(
                self.slack_webhook,
                json={"text": report, "attachments": [{"text": json.dumps(payload)}]},
                timeout=10,
            )
            return True
        except Exception as exc:
            logger.exception("slack_dispatch_failed: %s", exc)
            return False

    def _log_cloud(self, report: str, payload: dict) -> bool:
        logger.warning(
            "sentinel_alert event=%s severity=%s",
            payload.get("type"),
            payload.get("severity"),
            extra={"sentinel_report": report, "sentinel_payload": payload},
        )
        return True

    def _publish_pubsub(self, payload: dict) -> bool:
        if not self.project_id:
            return False
        try:
            from google.cloud import pubsub_v1

            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(self.project_id, self.alerts_topic)
            publisher.publish(topic_path, json.dumps(payload).encode("utf-8")).result(
                timeout=15
            )
            return True
        except Exception as exc:
            logger.exception("pubsub_dispatch_failed: %s", exc)
            return False
