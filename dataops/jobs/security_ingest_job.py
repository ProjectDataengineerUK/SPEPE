"""Cloud Run Job: Segurança Pública → Bronze layer.

Fontes:
  - IVS (Índice de Vulnerabilidade Social) — IPEADATA API, nível municipal
  - Atlas da Violência (taxa de homicídios) — IPEA/FBSP, CSV municipal
  - SINESP ocorrências — dados.gov.br, quando disponível
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dataops.bronze_writer import write_bronze
from dataops.clients.ibge_client import load_municipios
from dataops.clients.seguranca_client import build_seguranca_dataframe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.security_ingest")

DEFAULT_YEAR = int(os.environ.get("DEFAULT_ANO", "2022"))
DEFAULT_UF = os.environ.get("DEFAULT_UF", "SP")


def main(uf: str, year: int) -> None:
    logger.info("Security ingest job: UF=%s ano=%d", uf, year)
    cache_dir = Path("data/bronze/security")

    # Busca códigos IBGE dos municípios da UF
    df_mun = load_municipios(uf)
    if df_mun.empty:
        logger.error("Sem municípios IBGE para UF=%s — abortando", uf)
        return

    municipios_ibge = df_mun["cd_municipio_ibge"].astype(int).tolist()
    logger.info("%d municípios IBGE para UF=%s", len(municipios_ibge), uf)

    df = build_seguranca_dataframe(uf, year, municipios_ibge, cache_dir)

    if df.empty:
        logger.warning("Nenhum dado de segurança obtido para UF=%s ano=%d", uf, year)
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    out_path = write_bronze(
        df=df,
        source="security",
        year=year,
        uf=uf,
        filename=f"seguranca_{uf.upper()}_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("Security Bronze: %s (%d rows)", out_path, len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPEPE Security Ingest Job")
    parser.add_argument("--uf", default=DEFAULT_UF)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    args = parser.parse_args()
    main(uf=args.uf, year=args.year)
