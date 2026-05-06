"""Cloud Run Job: CadÚnico + Bolsa Família → Bronze layer (nacional, por município)."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.cadunico_client import fetch_cadunico_municipio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.cadunico_ingest")

DEFAULT_YEAR = int(os.environ.get("CADUNICO_YEAR", os.environ.get("DEFAULT_ANO", "2022")))


def main(year: int) -> None:
    logger.info("CadÚnico ingest job: ano=%d", year)

    df = fetch_cadunico_municipio(year)

    if df.empty:
        logger.warning("Nenhum dado CadÚnico para ano=%d", year)
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    out_path = write_bronze(
        df=df,
        source="cadunico",
        year=year,
        uf="BR",
        filename=f"cadunico_BR_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("CadÚnico Bronze: %s (%d municípios)", out_path, len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPEPE CadÚnico Ingest Job")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    args = parser.parse_args()
    main(year=args.year)
