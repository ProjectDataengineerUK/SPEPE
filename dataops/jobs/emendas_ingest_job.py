"""Cloud Run Job: Emendas Parlamentares → Bronze layer (nacional)."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.emendas_client import fetch_emendas_parlamentares

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.emendas_ingest")

_YEARS_ENV = os.environ.get("EMENDAS_YEARS", os.environ.get("EMENDAS_YEAR", os.environ.get("DEFAULT_ANO", "2022")))
DEFAULT_YEARS = [int(y.strip()) for y in _YEARS_ENV.split(",") if y.strip()]


def main(year: int) -> None:
    logger.info("Emendas ingest job: ano=%d", year)

    df = fetch_emendas_parlamentares(year)

    if df.empty:
        logger.warning("Nenhuma emenda para ano=%d", year)
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    out_path = write_bronze(
        df=df,
        source="emendas",
        year=year,
        uf="BR",
        filename=f"emendas_BR_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("Emendas Bronze: %s (%d registros)", out_path, len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPEPE Emendas Parlamentares Ingest Job")
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()

    years = [args.year] if args.year else DEFAULT_YEARS
    for y in years:
        main(year=y)
