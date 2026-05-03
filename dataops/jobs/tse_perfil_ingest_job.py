"""Cloud Run Job: TSE Perfil do Eleitorado → Bronze layer."""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.tse_perfil_client import (
    build_perfil_municipio,
    fetch_perfil_raw_zip,
    parse_perfil_from_zip,
)

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


def _build_agg_from_raw(raw: bytes, uf: str, year: int) -> pd.DataFrame:
    """Build municipality-level aggregate from pre-downloaded ZIP bytes."""
    df = parse_perfil_from_zip(raw, uf, year)
    if df.empty:
        return pd.DataFrame()

    group_cols = [
        c
        for c in (
            "cd_municipio",
            "nm_municipio",
            "sg_uf",
            "ds_genero",
            "ds_faixa_etaria",
            "ds_grau_escolaridade",
            "ds_estado_civil",
            "ano",
        )
        if c in df.columns
    ]
    agg = df.groupby(group_cols, as_index=False)["qt_eleitores"].sum()
    agg["ingested_at"] = pd.Timestamp.utcnow()
    return agg


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--years", nargs="+", type=int, default=_YEARS)
    args = parser.parse_args()

    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    use_gcs = bool(os.environ.get("GCS_BUCKET"))

    if len(ufs) > 1:
        # Batch mode: download each year's national ZIP once, parse all UFs from it
        ok = fail = 0
        for year in args.years:
            raw = fetch_perfil_raw_zip(year)
            if raw is None:
                logger.warning("Perfil não disponível: ano=%d — pulando", year)
                fail += len(ufs)
                continue
            for uf in ufs:
                logger.info("TSE Perfil Eleitorado: %s/%d", uf, year)
                try:
                    df = _build_agg_from_raw(raw, uf, year)
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
        logger.info("TSE Perfil ingest ALL concluído: %d ok / %d falhas", ok, fail)
        if fail > 0 and ok == 0:
            sys.exit(1)
    else:
        main(ufs[0], args.years)
