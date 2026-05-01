"""Cloud Run Job: Download TSE data → Bronze layer."""

from __future__ import annotations

import logging
import os
import sys

from dataops.bronze_writer import write_bronze_from_file
from dataops.clients.tse_client import download_tse_resultados

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.tse_ingest")


def main(uf: str, year: int) -> None:
    logger.info("TSE ingest job: UF=%s ano=%d", uf, year)

    try:
        parquet_path = download_tse_resultados(uf, year)
    except Exception as exc:
        logger.error("Download TSE falhou: %s", exc)
        sys.exit(1)

    out_path = write_bronze_from_file(
        src_path=parquet_path,
        source="tse",
        year=year,
        uf=uf,
        filename=f"resultados_{uf.upper()}_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )

    logger.info("TSE ingest concluído: %s", out_path)


_ALL_UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TSE ingest job")
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--year", type=int, default=int(os.environ.get("DEFAULT_ANO", "2022")))
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf, args.year)
