"""Cloud Run Job: IBGE SIDRA + Localidades → Bronze layer."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.ibge_client import fetch_sidra_indicators, load_municipios

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.ibge_sync")

UF_CODES = {
    "AC": "12", "AL": "27", "AP": "16", "AM": "13", "BA": "29", "CE": "23",
    "DF": "53", "ES": "32", "GO": "52", "MA": "21", "MT": "51", "MS": "50",
    "MG": "31", "PA": "15", "PB": "25", "PR": "41", "PE": "26", "PI": "22",
    "RJ": "33", "RN": "24", "RS": "43", "RO": "11", "RR": "14", "SC": "42",
    "SP": "35", "SE": "28", "TO": "17",
}

DEFAULT_INDICADORES = [
    "populacao", "renda_media", "taxa_desemprego", "pct_analfabetos",
]


def main(uf: str) -> None:
    logger.info("IBGE sync job: UF=%s", uf)
    cache_dir = Path("data/bronze/ibge")
    uf_code = UF_CODES.get(uf.upper(), "35")

    rows = fetch_sidra_indicators(uf, DEFAULT_INDICADORES, cache_dir, uf_code=uf_code)

    if rows:
        df = pd.DataFrame(rows)
        out_path = write_bronze(
            df=df,
            source="ibge",
            year=2022,
            uf=uf,
            filename=f"indicadores_{uf.upper()}_2022.parquet",
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
            year=2022,
            uf=uf,
            filename=f"municipios_{uf.upper()}.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("Municípios Bronze: %s (%d rows)", out_path, len(df_mun))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    args = parser.parse_args()
    main(args.uf)
