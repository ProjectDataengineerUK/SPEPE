"""Cloud Run Job: TSE Candidaturas (bens + financiamento + dados cadastrais) → Bronze."""

from __future__ import annotations

import logging
import os
import sys

from dataops.bronze_writer import write_bronze
from dataops.clients.tse_candidaturas_client import (
    build_candidaturas_dataframe,
    build_candidaturas_from_raw,
    fetch_candidaturas_raw_zips,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.tse_candidaturas_ingest")

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
_YEARS = [2018, 2022, 2026]


def main(uf: str, years: list[int]) -> None:
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    ok = 0
    fail = 0

    for year in years:
        logger.info("TSE Candidaturas: %s/%d", uf, year)
        try:
            df = build_candidaturas_dataframe(uf, year)
            if df.empty:
                logger.warning("Candidaturas vazio: %s/%d", uf, year)
                fail += 1
                continue
            write_bronze(
                df=df,
                source="tse_candidaturas",
                year=year,
                uf=uf.upper(),
                filename=f"candidaturas_{uf.upper()}_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("Candidaturas Bronze OK: %s/%d — %d candidatos", uf, year, len(df))
            ok += 1
        except Exception as exc:
            logger.error("Candidaturas falhou %s/%d: %s", uf, year, exc)
            fail += 1

    logger.info("TSE Candidaturas ingest concluído: %d ok / %d falhas", ok, fail)
    if fail > 0 and ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--years", nargs="+", type=int, default=_YEARS)
    args = parser.parse_args()

    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    use_gcs = bool(os.environ.get("GCS_BUCKET"))

    if len(ufs) > 1:
        # Batch mode: download each year's 3 national ZIPs once, filter all UFs
        ok = fail = 0
        for year in args.years:
            logger.info("TSE Candidaturas: baixando ZIPs nacionais ano=%d", year)
            raw_cand, raw_bens, raw_rec = fetch_candidaturas_raw_zips(year)
            if raw_cand is None:
                logger.warning("Candidatos não disponível: ano=%d — pulando", year)
                fail += len(ufs)
                continue
            for uf in ufs:
                logger.info("TSE Candidaturas: %s/%d", uf, year)
                try:
                    df = build_candidaturas_from_raw(raw_cand, raw_bens, raw_rec, uf, year)
                    if df.empty:
                        logger.warning("Candidaturas vazio: %s/%d", uf, year)
                        fail += 1
                        continue
                    write_bronze(
                        df=df,
                        source="tse_candidaturas",
                        year=year,
                        uf=uf.upper(),
                        filename=f"candidaturas_{uf.upper()}_{year}.parquet",
                        use_gcs=use_gcs,
                    )
                    logger.info("Candidaturas Bronze OK: %s/%d — %d candidatos", uf, year, len(df))
                    ok += 1
                except Exception as exc:
                    logger.error("Candidaturas falhou %s/%d: %s", uf, year, exc)
                    fail += 1
        logger.info("TSE Candidaturas ingest ALL concluído: %d ok / %d falhas", ok, fail)
        if fail > 0 and ok == 0:
            sys.exit(1)
    else:
        main(ufs[0], args.years)
