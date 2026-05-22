"""Cloud Run Job: IBGE SIDRA + Localidades → Bronze layer."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.ibge_client import (
    fetch_ipeadata_atlas_municipios,
    fetch_sidra_indicators,
    load_municipios,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.ibge_sync")

UF_CODES = {
    "AC": "12",
    "AL": "27",
    "AP": "16",
    "AM": "13",
    "BA": "29",
    "CE": "23",
    "DF": "53",
    "ES": "32",
    "GO": "52",
    "MA": "21",
    "MT": "51",
    "MS": "50",
    "MG": "31",
    "PA": "15",
    "PB": "25",
    "PR": "41",
    "PE": "26",
    "PI": "22",
    "RJ": "33",
    "RN": "24",
    "RS": "43",
    "RO": "11",
    "RR": "14",
    "SC": "42",
    "SP": "35",
    "SE": "28",
    "TO": "17",
}

DEFAULT_INDICADORES = [
    "populacao",
    "taxa_alfabetizacao",
    "pct_analfabetos",
    "pct_0_14",
    "pct_15_29",
    "pct_30_59",
    "pct_60_mais",
    # "pct_urbano" removido — tabela SIDRA 9714 N6 não disponível via API (Censo 2022 consolidando)
    "pct_catolico",
    "pct_sem_religiao",
]


def main(uf: str, year: int | None = None) -> None:
    if year is None:
        year = int(os.environ.get("IBGE_YEAR", os.environ.get("DEFAULT_ANO", "2022")))
    logger.info("IBGE sync job: UF=%s year=%d", uf, year)
    cache_dir = Path("data/bronze/ibge")
    uf_code = UF_CODES.get(uf.upper(), "35")

    rows = fetch_sidra_indicators(uf, DEFAULT_INDICADORES, cache_dir, uf_code=uf_code)

    if rows:
        df = pd.DataFrame(rows)
        out_path = write_bronze(
            df=df,
            source="ibge",
            year=year,
            uf=uf,
            filename=f"indicadores_{uf.upper()}_{year}.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("IBGE SIDRA Bronze: %s (%d rows)", out_path, len(df))
    else:
        logger.warning("Nenhum dado IBGE SIDRA retornado para %s", uf)

    df_mun = load_municipios(uf)
    if not df_mun.empty:
        out_path = write_bronze(
            df=df_mun,
            source="ibge",
            year=year,
            uf=uf,
            filename=f"municipios_{uf.upper()}.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("Municípios Bronze: %s (%d rows)", out_path, len(df_mun))

    # IPEADATA Atlas (IDHM, Renda per capita, Gini, Extrema Pobreza) — Censo 2010
    # Fetch once per job run (all municipalities in Brazil). Only UF=ALL triggers the fetch
    # to avoid redundant downloads; the single parquet covers all UFs.
    if uf.upper() == "ALL" or not _atlas_already_fetched(year, bool(os.environ.get("GCS_BUCKET"))):
        df_atlas = fetch_ipeadata_atlas_municipios()
        if not df_atlas.empty:
            out_path = write_bronze(
                df=df_atlas,
                source="ibge",
                year=year,
                uf="BR",
                filename="atlas_municipios_BR.parquet",
                use_gcs=bool(os.environ.get("GCS_BUCKET")),
            )
            logger.info("IPEADATA Atlas Bronze: %s (%d rows)", out_path, len(df_atlas))


def _atlas_already_fetched(year: int, use_gcs: bool) -> bool:
    """Return True if atlas_municipios_BR.parquet already exists locally."""
    if use_gcs:
        return False  # always re-fetch in GCS mode (Bronze is immutable)
    local = Path("data/bronze/ibge") / str(year) / "BR" / "atlas_municipios_BR.parquet"
    return local.exists()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()
    ufs = list(UF_CODES.keys()) if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf, year=args.year)
