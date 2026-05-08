"""
SPEPE Sentinel — Pub/Sub pull listener.

Async consumer for `spepe-sentinel-events` that broadcasts each received
message to the SSE subscribers via the provided `broadcast` callable.

The consumer is a no-op when `gcp_project_id` is empty or "local" — useful
for local dev and CI.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from config.settings import settings

logger = logging.getLogger("spepe.sentinel.pubsub")

SUBSCRIPTION_ID = "spepe-sentinel-events-sub"

BroadcastFn = Callable[[str, dict], Awaitable[None]]


async def consume_sentinel_pubsub(broadcast: BroadcastFn) -> None:
    """
    Pull-loop for the sentinel Pub/Sub subscription.

    The function runs forever; cancel the surrounding task to stop it.
    """
    if not settings.gcp_project_id or settings.gcp_project_id == "local":
        logger.info("Sentinel Pub/Sub disabled (no gcp_project_id)")
        return

    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(settings.gcp_project_id, SUBSCRIPTION_ID)
    logger.info("Sentinel Pub/Sub consumer started — %s", sub_path)

    while True:
        try:
            response = await asyncio.to_thread(
                subscriber.pull,
                request={"subscription": sub_path, "max_messages": 10},
                timeout=20.0,
            )
            ack_ids: list[str] = []
            for received in response.received_messages:
                try:
                    payload = json.loads(received.message.data.decode("utf-8"))
                except Exception as exc:
                    logger.warning("invalid sentinel pubsub payload: %s", exc)
                    ack_ids.append(received.ack_id)
                    continue
                event_type = received.message.attributes.get("event_type", "job_status_changed")
                try:
                    await broadcast(event_type, payload)
                except Exception as exc:
                    logger.warning("broadcast failed for %s: %s", event_type, exc)
                ack_ids.append(received.ack_id)
            if ack_ids:
                await asyncio.to_thread(
                    subscriber.acknowledge,
                    request={"subscription": sub_path, "ack_ids": ack_ids},
                )
        except asyncio.CancelledError:
            logger.info("Sentinel Pub/Sub consumer cancelled")
            raise
        except Exception as exc:
            logger.warning("sentinel pubsub pull failed: %s", exc)
            await asyncio.sleep(5)
