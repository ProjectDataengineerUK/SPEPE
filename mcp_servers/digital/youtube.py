"""YouTube Data API v3 — aggregate views per official candidate channel."""

from __future__ import annotations

import logging
from pathlib import Path

import requests

logger = logging.getLogger("spepe.mcp.digital.youtube")

YT_API_BASE = "https://www.googleapis.com/youtube/v3"
CACHE_DIR = Path("data/bronze/digital/youtube")


def fetch_channel_stats(channel_id: str, api_key: str) -> dict:
    if not api_key:
        logger.warning("YOUTUBE_API_KEY não configurada.")
        return {}

    params = {
        "part": "statistics",
        "id": channel_id,
        "key": api_key,
    }
    try:
        r = requests.get(f"{YT_API_BASE}/channels", params=params, timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            stats = items[0].get("statistics", {})
            return {
                "channel_id": channel_id,
                "view_count": int(stats.get("viewCount", 0)),
                "subscriber_count": int(stats.get("subscriberCount", 0)),
                "video_count": int(stats.get("videoCount", 0)),
            }
    except requests.RequestException as e:
        logger.warning(f"YouTube API error: {e}")
    return {}


def fetch_video_views_aggregate(
    channel_id: str,
    api_key: str,
    published_after: str = "2022-06-01T00:00:00Z",
    published_before: str = "2022-10-31T23:59:59Z",
) -> dict:
    """Return total views for a channel's videos in the given period (aggregate, not per-video)."""
    if not api_key:
        return {}

    params = {
        "part": "id",
        "channelId": channel_id,
        "publishedAfter": published_after,
        "publishedBefore": published_before,
        "type": "video",
        "maxResults": 50,
        "key": api_key,
    }
    total_views = 0
    try:
        r = requests.get(f"{YT_API_BASE}/search", params=params, timeout=15)
        r.raise_for_status()
        video_ids = [item["id"]["videoId"] for item in r.json().get("items", [])]

        if video_ids:
            stats_params = {
                "part": "statistics",
                "id": ",".join(video_ids),
                "key": api_key,
            }
            r2 = requests.get(f"{YT_API_BASE}/videos", params=stats_params, timeout=15)
            r2.raise_for_status()
            for item in r2.json().get("items", []):
                total_views += int(item.get("statistics", {}).get("viewCount", 0))
    except requests.RequestException as e:
        logger.warning(f"YouTube views fetch error: {e}")

    return {"channel_id": channel_id, "total_views_period": total_views}
