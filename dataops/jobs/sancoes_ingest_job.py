"""Cloud Run Job: CEIS + CNEP (sanções federais) → Bronze layer."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.sancoes_client import fetch_sancoes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("spepe.jobs.sancoes_ingest")

# CEIS/CNEP não têm recorte temporal — contém todas as sanções ativas e históricas.
# Usamos o ano de referência apenas para nomear o arquivo Bronze.
REF_YEAR = int(os.environ.get("SANCOES_YEAR", "2026"))


def main() -> None:
    logger.info("Sanções ingest job (CEIS + CNEP): ref_year=%d", REF_YEAR)

    df = fetch_sancoes()

    if df.empty:
        logger.warning("Nenhuma sanção retornada")
        return

    df["ingested_at"] = datetime.now(timezone.utc).isoformat()
    df["ano_referencia"] = REF_YEAR

    out_path = write_bronze(
        df=df,
        source="sancoes",
        year=REF_YEAR,
        uf="BR",
        filename=f"sancoes_BR_{REF_YEAR}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("Sanções Bronze: %s (%d registros)", out_path, len(df))


if __name__ == "__main__":
    main()
