"""Cloud Run Job: Digital signal (Google Trends, Meta Ads) → Bronze layer."""

from __future__ import annotations

import logging
import os

import pandas as pd

from dataops.bronze_writer import write_bronze

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("spepe.jobs.digital_ingest")

CANDIDATES_2022 = ["Lula", "Bolsonaro", "Ciro Gomes", "Simone Tebet"]
TIMEFRAME_2022 = "2022-06-01 2022-10-30"


def main() -> None:
    _ingest_trends()
    _ingest_meta_ads()


def _ingest_trends() -> None:
    from dataops.clients.digital_client import fetch_trends

    logger.info("Ingestão Google Trends...")
    df = fetch_trends(CANDIDATES_2022, timeframe=TIMEFRAME_2022, geo="BR")
    if df.empty:
        logger.warning("Google Trends: nenhum dado retornado.")
        return

    df_reset = df.reset_index() if df.index.name else df
    write_bronze(
        df=df_reset,
        source="digital",
        year=2022,
        uf="BR",
        filename="google_trends_candidatos_2022.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info(f"Google Trends Bronze: {len(df)} linhas")


def _ingest_meta_ads() -> None:
    token = os.environ.get("META_APP_TOKEN", "")
    if not token:
        logger.warning("META_APP_TOKEN não configurado — Meta Ads pulado.")
        return

    from dataops.clients.digital_client import fetch_meta_ads

    logger.info("Ingestão Meta Ad Library...")
    dfs = []
    for candidate in CANDIDATES_2022:
        df = fetch_meta_ads(candidate, access_token=token)
        if not df.empty:
            dfs.append(df)

    if dfs:
        df_all = pd.concat(dfs, ignore_index=True)
        write_bronze(
            df=df_all,
            source="digital",
            year=2022,
            uf="BR",
            filename="meta_ads_candidatos_2022.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info(f"Meta Ads Bronze: {len(df_all)} linhas")


if __name__ == "__main__":
    main()
