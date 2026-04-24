from __future__ import annotations

import base64
import json
import logging
from typing import Callable

from sentinel.events.event_types import SentinelEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Deserializes Pub/Sub events and dispatches to registered handlers.

    Supports two modes:
      - Pull: `pull_and_dispatch(subscription_id)` reads from Cloud Pub/Sub.
      - Push: `handle_pubsub_envelope(envelope)` decodes an HTTP push payload.
    """

    def __init__(self, project_id: str | None = None):
        self.project_id = project_id
        self._handlers: list[Callable[[SentinelEvent], None]] = []

    def subscribe(self, handler: Callable[[SentinelEvent], None]) -> None:
        self._handlers.append(handler)

    def _dispatch(self, event: SentinelEvent) -> None:
        for handler in self._handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.exception("handler_failed: %s event_type=%s", exc, event.type)

    def handle_pubsub_envelope(self, envelope: dict) -> SentinelEvent | None:
        message = envelope.get("message", {})
        data_b64 = message.get("data")
        if not data_b64:
            logger.warning("empty_pubsub_message")
            return None
        raw = base64.b64decode(data_b64).decode("utf-8")
        payload = json.loads(raw)
        event = SentinelEvent.from_dict(payload)
        self._dispatch(event)
        return event

    def pull_and_dispatch(self, subscription_id: str, max_messages: int = 10) -> int:
        try:
            from google.cloud import pubsub_v1
        except ImportError:
            logger.warning("pubsub_client_unavailable")
            return 0

        subscriber = pubsub_v1.SubscriberClient()
        sub_path = subscriber.subscription_path(self.project_id, subscription_id)
        response = subscriber.pull(
            request={"subscription": sub_path, "max_messages": max_messages},
            timeout=10,
        )
        ack_ids: list[str] = []
        for received in response.received_messages:
            try:
                payload = json.loads(received.message.data.decode("utf-8"))
                event = SentinelEvent.from_dict(payload)
                self._dispatch(event)
                ack_ids.append(received.ack_id)
            except Exception as exc:
                logger.exception("event_processing_failed: %s", exc)
        if ack_ids:
            subscriber.acknowledge(
                request={"subscription": sub_path, "ack_ids": ack_ids}
            )
        return len(ack_ids)
