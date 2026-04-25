"""Google Trends scraper via pytrends — LGPD-safe aggregate by region."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.mcp.digital.google_trends")

CACHE_DIR = Path("data/bronze/digital/google_trends")


def fetch_trends(
    keywords: list[str],
    timeframe: str = "2022-06-01 2022-10-30",
    geo: str = "BR",
) -> pd.DataFrame:
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.warning("pytrends não instalado. Execute: pip install pytrends")
        return pd.DataFrame()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = "_".join(keywords).replace(" ", "_")
    cache_path = CACHE_DIR / f"trends_{cache_key}_{geo}.parquet"

    if cache_path.exists():
        return pd.read_parquet(cache_path)

    pytrends = TrendReq(hl="pt-BR", tz=180)
    try:
        pytrends.build_payload(keywords[:5], timeframe=timeframe, geo=geo)
        df = pytrends.interest_by_region(resolution="REGION", inc_low_vol=True)
        time.sleep(1)
        if not df.empty:
            df.to_parquet(cache_path, index=False)
        return df
    except Exception as e:
        logger.warning(f"Google Trends error: {e}")
        return pd.DataFrame()


def fetch_trends_over_time(
    keywords: list[str],
    timeframe: str = "2022-06-01 2022-10-30",
    geo: str = "BR",
) -> pd.DataFrame:
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return pd.DataFrame()

    pytrends = TrendReq(hl="pt-BR", tz=180)
    try:
        pytrends.build_payload(keywords[:5], timeframe=timeframe, geo=geo)
        df = pytrends.interest_over_time()
        time.sleep(1)
        return df
    except Exception as e:
        logger.warning(f"Google Trends over time error: {e}")
        return pd.DataFrame()
