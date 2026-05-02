"""Cloud Run Job: TSE Perfil do Eleitorado → Bronze layer."""

from __future__ import annotations

import logging
import os
import sys

from dataops.bronze_writer import write_bronze
from dataops.clients.tse_perfil_client import build_perfil_municipio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.tse_perfil_ingest")

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
_YEARS = [2018, 2020, 2022, 2024]


def main(uf: str, years: list[int]) -> None:
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    ok = 0
    fail = 0

    for year in years:
        logger.info("TSE Perfil Eleitorado: %s/%d", uf, year)
        try:
            df = build_perfil_municipio(uf, year)
            if df.empty:
                logger.warning("Perfil vazio: %s/%d", uf, year)
                fail += 1
                continue
            write_bronze(
                df=df,
                source="tse_perfil",
                year=year,
                uf=uf.upper(),
                filename=f"perfil_eleitorado_{uf.upper()}_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("Perfil Bronze OK: %s/%d — %d linhas", uf, year, len(df))
            ok += 1
        except Exception as exc:
            logger.error("Perfil falhou %s/%d: %s", uf, year, exc)
            fail += 1

    logger.info("TSE Perfil ingest concluído: %d ok / %d falhas", ok, fail)
    if fail > 0 and ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--years", nargs="+", type=int, default=_YEARS)
    args = parser.parse_args()

    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf, args.years)
