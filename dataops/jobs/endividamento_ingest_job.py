"""Cloud Run Job: Banco Central endividamento → Bronze layer."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.bacen_client import build_endividamento_dataframe
from dataops.clients.ibge_client import load_municipios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.endividamento_ingest")

DEFAULT_UF = os.environ.get("DEFAULT_UF", "SP")
DEFAULT_YEAR_START = int(os.environ.get("ENDIVIDAMENTO_YEAR_START", "2025"))
DEFAULT_YEAR_END = int(os.environ.get("ENDIVIDAMENTO_YEAR_END", "2026"))


def main(uf: str, year_start: int, year_end: int) -> None:
    logger.info("Endividamento ingest: UF=%s anos=%d-%d", uf, year_start, year_end)

    df_mun = load_municipios(uf)
    if df_mun.empty:
        logger.error("Sem municípios IBGE para UF=%s", uf)
        return

    municipios_ibge = df_mun["cd_municipio_ibge"].astype(int).tolist()

    df = build_endividamento_dataframe(uf, year_start, year_end, municipios_ibge)

    if df.empty:
        logger.warning("Sem dados BCB para UF=%s %d-%d", uf, year_start, year_end)
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    out_path = write_bronze(
        df=df,
        source="endividamento",
        year=year_end,
        uf=uf,
        filename=f"endividamento_{uf.upper()}_{year_start}_{year_end}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("BCB endividamento Bronze: %s (%d rows)", out_path, len(df))


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
    parser = argparse.ArgumentParser(description="SPEPE Endividamento Ingest Job")
    parser.add_argument("--uf", default=DEFAULT_UF)
    parser.add_argument("--year-start", type=int, default=DEFAULT_YEAR_START)
    parser.add_argument("--year-end", type=int, default=DEFAULT_YEAR_END)
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf=uf, year_start=args.year_start, year_end=args.year_end)
