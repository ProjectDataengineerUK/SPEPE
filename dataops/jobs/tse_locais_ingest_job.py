"""Cloud Run Job: TSE Locais de Votação → Bronze layer (snapshot ATUAL por UF)."""

from __future__ import annotations

import logging
import os

from dataops.bronze_writer import write_bronze_from_file
from dataops.clients.tse_client import download_eleitorado_locais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.tse_locais_ingest")

_ALL_UFS = [
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
]


def main(uf: str) -> None:
    import datetime

    snapshot_year = datetime.date.today().year
    logger.info("TSE locais ingest: UF=%s snapshot=%d", uf, snapshot_year)

    try:
        parquet_path = download_eleitorado_locais(uf)
    except Exception as exc:
        logger.error("Download locais %s falhou: %s", uf, exc)
        return

    out = write_bronze_from_file(
        src_path=parquet_path,
        source="tse_locais",
        year=snapshot_year,
        uf=uf,
        filename=f"locais_{uf.upper()}_ATUAL.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("Locais Bronze OK: %s/%d → %s", uf, snapshot_year, out)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TSE Locais de Votação ingest job")
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf)
