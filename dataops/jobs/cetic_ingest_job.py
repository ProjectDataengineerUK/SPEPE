"""Cloud Run Job: CETIC TIC Domicílios → Bronze layer (acesso digital)."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.cetic_client import build_digital_access_dataframe
from dataops.clients.ibge_client import load_municipios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.cetic_ingest")

DEFAULT_UF = os.environ.get("DEFAULT_UF", "SP")
DEFAULT_YEAR = int(os.environ.get("DEFAULT_ANO", "2023"))


def main(uf: str, year: int) -> None:
    logger.info("CETIC ingest job: UF=%s ano=%d", uf, year)

    df_mun = load_municipios(uf)
    if df_mun.empty:
        logger.error("Sem municípios IBGE para UF=%s — abortando", uf)
        return

    municipios_ibge = df_mun["cd_municipio_ibge"].astype(int).tolist()

    df = build_digital_access_dataframe(uf, year, municipios_ibge)

    if df.empty:
        logger.warning("Nenhum dado CETIC para UF=%s ano=%d", uf, year)
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    out_path = write_bronze(
        df=df,
        source="cetic",
        year=year,
        uf=uf,
        filename=f"tic_domicilios_{uf.upper()}_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("CETIC Bronze: %s (%d rows)", out_path, len(df))


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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPEPE CETIC TIC Domicílios Ingest Job")
    parser.add_argument("--uf", default=DEFAULT_UF)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf=uf, year=args.year)
