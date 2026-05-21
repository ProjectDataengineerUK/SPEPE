"""Hybrid profile monitoring — scraping + official APIs.

Estratégia por plataforma:
  Instagram   — Instaloader (público, sem auth) → métricas básicas do perfil
  X/Twitter   — API v2 user lookup (X_BEARER_TOKEN) → followers/following/tweet_count
  YouTube     — Data API v3 (YOUTUBE_API_KEY) → subscriber_count, view_count
  TikTok      — scraping público (playwright headless) → divert count, likes
  Facebook    — Graph API /me/accounts (META_APP_TOKEN) → page fans

Uso:
    from dataops.clients.profile_scraper import fetch_profile_metrics

    metrics = fetch_profile_metrics(
        platform="x",
        handle="eduardopaes",   # sem @
    )
    # -> ProfileMetrics(handle, platform, followers, following, posts, ...)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger("spepe.clients.profile_scraper")

_RATE_LIMIT_SLEEP = float(os.environ.get("SCRAPER_RATE_SLEEP", "1.5"))


@dataclass
class ProfileMetrics:
    handle: str
    platform: str
    followers: Optional[int] = None
    following: Optional[int] = None
    posts_count: Optional[int] = None
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    engagement_rate: Optional[float] = None
    verified: Optional[bool] = None
    bio: Optional[str] = None
    scrape_ts: str = field(default_factory=lambda: _now_iso())
    source: str = "profile_scraper"
    error: Optional[str] = None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ── X / Twitter ────────────────────────────────────────────────────────────────


def _fetch_x_profile(handle: str) -> ProfileMetrics:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return ProfileMetrics(handle=handle, platform="x", error="X_BEARER_TOKEN ausente")

    url = "https://api.twitter.com/2/users/by/username/" + handle
    params = {
        "user.fields": "public_metrics,verified,description",
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        pm = data.get("public_metrics", {})
        followers = pm.get("followers_count")
        following = pm.get("following_count")
        tweets = pm.get("tweet_count")
        likes_total = pm.get("like_count", 0)
        eng = round(likes_total / tweets, 2) if tweets else None
        return ProfileMetrics(
            handle=handle,
            platform="x",
            followers=followers,
            following=following,
            posts_count=tweets,
            engagement_rate=eng,
            verified=data.get("verified"),
            bio=data.get("description"),
        )
    except Exception as exc:
        logger.warning("X profile %s: %s", handle, exc)
        return ProfileMetrics(handle=handle, platform="x", error=str(exc))


# ── Instagram (Instaloader public) ────────────────────────────────────────────


def _fetch_instagram_profile(handle: str) -> ProfileMetrics:
    try:
        import instaloader  # type: ignore[import]

        loader = instaloader.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
        )
        profile = instaloader.Profile.from_username(loader.context, handle)
        followers = profile.followers
        following = profile.followees
        posts = profile.mediacount
        bio = profile.biography
        return ProfileMetrics(
            handle=handle,
            platform="instagram",
            followers=followers,
            following=following,
            posts_count=posts,
            bio=bio,
        )
    except ImportError:
        return ProfileMetrics(
            handle=handle, platform="instagram", error="instaloader não instalado"
        )
    except Exception as exc:
        logger.warning("Instagram profile %s: %s", handle, exc)
        return ProfileMetrics(handle=handle, platform="instagram", error=str(exc))


# ── YouTube ────────────────────────────────────────────────────────────────────


def _fetch_youtube_channel(handle: str) -> ProfileMetrics:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return ProfileMetrics(handle=handle, platform="youtube", error="YOUTUBE_API_KEY ausente")

    # Resolve channel id from handle (@handle) or custom URL
    search_url = "https://www.googleapis.com/youtube/v3/search"
    try:
        resp = requests.get(
            search_url,
            params={"part": "snippet", "q": handle, "type": "channel", "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return ProfileMetrics(handle=handle, platform="youtube", error="canal não encontrado")
        channel_id = items[0]["id"]["channelId"]

        stats_url = "https://www.googleapis.com/youtube/v3/channels"
        r2 = requests.get(
            stats_url,
            params={"part": "statistics,snippet", "id": channel_id, "key": api_key},
            timeout=10,
        )
        r2.raise_for_status()
        ch_items = r2.json().get("items", [])
        if not ch_items:
            return ProfileMetrics(handle=handle, platform="youtube", error="stats não encontradas")
        stats = ch_items[0].get("statistics", {})
        snippet = ch_items[0].get("snippet", {})
        return ProfileMetrics(
            handle=handle,
            platform="youtube",
            followers=int(stats.get("subscriberCount", 0) or 0),
            posts_count=int(stats.get("videoCount", 0) or 0),
            avg_likes=None,
            bio=snippet.get("description", "")[:300],
        )
    except Exception as exc:
        logger.warning("YouTube channel %s: %s", handle, exc)
        return ProfileMetrics(handle=handle, platform="youtube", error=str(exc))


# ── Facebook ───────────────────────────────────────────────────────────────────


def _fetch_facebook_page(page_id: str) -> ProfileMetrics:
    token = os.environ.get("META_APP_TOKEN")
    if not token:
        return ProfileMetrics(handle=page_id, platform="facebook", error="META_APP_TOKEN ausente")

    url = f"https://graph.facebook.com/v20.0/{page_id}"
    try:
        resp = requests.get(
            url,
            params={"fields": "fan_count,followers_count,name,about", "access_token": token},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return ProfileMetrics(
            handle=page_id,
            platform="facebook",
            followers=data.get("followers_count") or data.get("fan_count"),
            bio=data.get("about", "")[:300],
        )
    except Exception as exc:
        logger.warning("Facebook page %s: %s", page_id, exc)
        return ProfileMetrics(handle=page_id, platform="facebook", error=str(exc))


# ── Public dispatcher ──────────────────────────────────────────────────────────

_FETCHERS = {
    "x": _fetch_x_profile,
    "twitter": _fetch_x_profile,
    "instagram": _fetch_instagram_profile,
    "youtube": _fetch_youtube_channel,
    "facebook": _fetch_facebook_page,
}


def fetch_profile_metrics(platform: str, handle: str) -> ProfileMetrics:
    """Fetch profile metrics for a single handle on a given platform."""
    fetcher = _FETCHERS.get(platform.lower())
    if not fetcher:
        return ProfileMetrics(
            handle=handle, platform=platform, error=f"plataforma {platform!r} não suportada"
        )
    time.sleep(_RATE_LIMIT_SLEEP)
    return fetcher(handle)


def fetch_precandidatos_metrics(
    uf: str = "RJ",
    platforms: list[str] | None = None,
) -> list[ProfileMetrics]:
    """Fetch profile metrics for all pre-candidates in a UF across given platforms."""
    from dataops.precandidatos_2026 import get_pages_dict

    platforms = platforms or ["x", "instagram", "youtube", "facebook"]
    pages = get_pages_dict(uf)

    results: list[ProfileMetrics] = []
    for platform in platforms:
        for handle in pages.get(platform, []):
            logger.info("Fetching %s/%s", platform, handle)
            m = fetch_profile_metrics(platform, handle)
            results.append(m)

    return results
