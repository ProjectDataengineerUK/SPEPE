"""Digital signals client — Google Trends (pytrends) + Meta Ad Library."""

from __future__ import annotations

import logging
import time

import pandas as pd

logger = logging.getLogger("spepe.clients.digital")


def fetch_trends(
    keywords: list[str],
    timeframe: str = "today 3-m",
    geo: str = "BR",
) -> pd.DataFrame:
    """Return Google Trends interest-over-time DataFrame for given keywords.

    Columns: date (index) + one column per keyword (0–100 scale).
    Returns empty DataFrame on failure.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.error("pytrends não instalado — pip install pytrends")
        return pd.DataFrame()

    try:
        pt = TrendReq(hl="pt-BR", tz=-180, timeout=(10, 30), retries=3, backoff_factor=0.5)

        # pytrends supports max 5 keywords per request
        frames: list[pd.DataFrame] = []
        chunks = [keywords[i : i + 5] for i in range(0, len(keywords), 5)]

        for chunk in chunks:
            pt.build_payload(chunk, timeframe=timeframe, geo=geo)
            df = pt.interest_over_time()
            if df.empty:
                logger.warning("Google Trends retornou vazio para: %s", chunk)
                continue
            df = df.drop(columns=["isPartial"], errors="ignore")
            frames.append(df)
            time.sleep(1)  # avoid rate limiting

        if not frames:
            return pd.DataFrame()

        result = frames[0]
        for df in frames[1:]:
            result = result.join(df, how="outer")

        logger.info("Google Trends: %d linhas, %d candidatos", len(result), len(result.columns))
        return result

    except Exception as exc:
        logger.error("Google Trends falhou: %s", exc)
        return pd.DataFrame()


def fetch_meta_ads(
    candidate: str,
    access_token: str,
    country: str = "BR",
    limit: int = 200,
) -> pd.DataFrame:
    """Fetch Meta Ad Library data for a candidate/topic.

    Returns DataFrame with ad spend and impression data.
    Returns empty DataFrame if token missing or API fails.
    """
    import requests

    url = "https://graph.facebook.com/v19.0/ads_archive"
    params = {
        "access_token": access_token,
        "ad_type": "POLITICAL_AND_ISSUE_ADS",
        "ad_reached_countries": country,
        "search_terms": candidate,
        "fields": (
            "id,ad_creative_bodies,ad_delivery_start_time,ad_delivery_stop_time,"
            "currency,impressions,spend,demographic_distribution,"
            "page_name,funding_entity"
        ),
        "limit": limit,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as exc:
        logger.error("Meta Ads falhou para '%s': %s", candidate, exc)
        return pd.DataFrame()

    if not data:
        logger.warning("Meta Ads: nenhum resultado para '%s'", candidate)
        return pd.DataFrame()

    rows = []
    for ad in data:
        spend = ad.get("spend", {})
        impr = ad.get("impressions", {})
        rows.append(
            {
                "candidate_search": candidate,
                "page_name": ad.get("page_name", ""),
                "funding_entity": ad.get("funding_entity", ""),
                "start_date": ad.get("ad_delivery_start_time", ""),
                "end_date": ad.get("ad_delivery_stop_time", ""),
                "spend_lower": _safe_float(spend.get("lower_bound")),
                "spend_upper": _safe_float(spend.get("upper_bound")),
                "impressions_lower": _safe_float(impr.get("lower_bound")),
                "impressions_upper": _safe_float(impr.get("upper_bound")),
                "currency": ad.get("currency", "BRL"),
            }
        )

    df = pd.DataFrame(rows)
    logger.info("Meta Ads: %d anúncios para '%s'", len(df), candidate)
    return df


def _safe_float(val: str | None) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
