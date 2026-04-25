"""Cloud Run Job: DIEESE Cesta Básica → Bronze layer (econômico municipal)."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.dieese_client import build_cesta_basica_dataframe
from dataops.clients.ibge_client import load_municipios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.dieese_ingest")

DEFAULT_UF = os.environ.get("DEFAULT_UF", "SP")
DEFAULT_YEAR = int(os.environ.get("DEFAULT_ANO", "2022"))


def main(uf: str, year: int) -> None:
    logger.info("DIEESE ingest job: UF=%s ano=%d", uf, year)

    df_mun = load_municipios(uf)
    if df_mun.empty:
        logger.error("Sem municípios IBGE para UF=%s — abortando", uf)
        return

    municipios_ibge = df_mun["cd_municipio_ibge"].astype(int).tolist()

    df = build_cesta_basica_dataframe(uf, year, municipios_ibge)

    if df.empty:
        logger.warning("Nenhum dado DIEESE para UF=%s ano=%d", uf, year)
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    out_path = write_bronze(
        df=df,
        source="dieese",
        year=year,
        uf=uf,
        filename=f"cesta_basica_{uf.upper()}_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("DIEESE Bronze: %s (%d rows)", out_path, len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPEPE DIEESE Ingest Job")
    parser.add_argument("--uf", default=DEFAULT_UF)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    args = parser.parse_args()
    main(uf=args.uf, year=args.year)
