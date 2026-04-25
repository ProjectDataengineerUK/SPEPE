"""Meta Ad Library reader — LGPD-safe aggregate by municipality.

Reads ad spend data at the municipality level (never individual).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger("spepe.mcp.digital.meta_ads")

AD_LIBRARY_URL = "https://graph.facebook.com/v19.0/ads_archive"
CACHE_DIR = Path("data/bronze/digital/meta_ads")


def fetch_ad_spend_by_candidate(
    candidate_name: str,
    country: str = "BR",
    access_token: str = "",
) -> pd.DataFrame:
    """Fetch Meta Ad Library data aggregated by region."""
    if not access_token:
        logger.warning("META_APP_TOKEN não configurado. Retornando dados vazios.")
        return pd.DataFrame()

    params = {
        "access_token": access_token,
        "ad_type": "POLITICAL_AND_ISSUE_ADS",
        "ad_reached_countries": country,
        "search_terms": candidate_name,
        "fields": "spend,impressions,region_distribution",
        "limit": 100,
    }
    try:
        r = requests.get(AD_LIBRARY_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
    except requests.RequestException as e:
        logger.warning(f"Meta Ads API error: {e}")
        return pd.DataFrame()

    rows = []
    for ad in data:
        spend = ad.get("spend", {})
        for region in ad.get("region_distribution", []):
            rows.append(
                {
                    "candidato": candidate_name,
                    "uf": region.get("region", ""),
                    "spend_usd": float(spend.get("lower_bound", 0) or 0),
                    "impressions_lower": float(
                        ad.get("impressions", {}).get("lower_bound", 0) or 0
                    ),
                }
            )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = (
            df.groupby(["candidato", "uf"])
            .agg(
                spend_usd=("spend_usd", "sum"),
                impressions_lower=("impressions_lower", "sum"),
            )
            .reset_index()
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        cache_path = CACHE_DIR / f"meta_ads_{candidate_name.replace(' ', '_')}.parquet"
        df.to_parquet(cache_path, index=False)

    return df
