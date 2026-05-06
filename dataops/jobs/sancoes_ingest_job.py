"""Cloud Run Job: CEIS + CNEP + CEAF + CEPIM (sanções federais) → Bronze layer."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from dataops.bronze_writer import write_bronze
from dataops.clients.sancoes_client import fetch_ceaf, fetch_ceis, fetch_cepim, fetch_cnep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.sancoes_ingest")

REF_YEAR = int(os.environ.get("SANCOES_YEAR", "2026"))
USE_GCS = bool(os.environ.get("GCS_BUCKET"))


def _write(df, sistema: str) -> None:
    if df.empty:
        logger.warning("%s: sem dados para gravar", sistema)
        return
    df["ingested_at"] = datetime.now(timezone.utc).isoformat()
    df["ano_referencia"] = REF_YEAR
    # Silver transformer espera arquivos nomeados {sistema}_BR_snapshot.parquet
    filename = f"{sistema.lower()}_BR_snapshot.parquet"
    out = write_bronze(
        df=df,
        source="sancoes",
        year=REF_YEAR,
        uf="BR",
        filename=filename,
        use_gcs=USE_GCS,
    )
    logger.info("%s Bronze: %s (%d registros)", sistema, out, len(df))


def main() -> None:
    logger.info("Sanções ingest job (CEIS + CNEP + CEAF + CEPIM): ref_year=%d", REF_YEAR)

    _write(fetch_ceis(), "ceis")
    _write(fetch_cnep(), "cnep")
    _write(fetch_ceaf(), "ceaf")
    _write(fetch_cepim(), "cepim")

    logger.info("Sanções ingest concluído")


if __name__ == "__main__":
    main()
