"""Cloud Run Job: instituto scrapers (Datafolha, Quaest, Ipespe, Paraná, CNT/MDA) → Bronze."""

from __future__ import annotations

import logging
import os
import sys

from dataops.bronze_writer import write_bronze
from dataops.clients.agencies_client import fetch_all_agencies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.agencies_ingest")


def main(year: int, cargo: str) -> None:
    use_gcs = bool(os.environ.get("GCS_BUCKET"))

    logger.info("Iniciando agencies ingest: ano=%d cargo=%s", year, cargo)

    df = fetch_all_agencies(year=year, cargo=cargo)

    if df.empty:
        logger.warning("agencies ingest: nenhum dado coletado para ano=%d cargo=%s", year, cargo)
        sys.exit(1)

    filename = f"pesquisas_agencias_{year}.parquet"
    path = write_bronze(
        df=df,
        source="pesquisas",
        year=year,
        uf="BR",
        filename=filename,
        use_gcs=use_gcs,
    )

    avg_score = (
        df["record_confidence_score"].mean()
        if "record_confidence_score" in df.columns
        else 0.0
    )
    logger.info(
        "Bronze agencies: %s (%d rows, score médio=%.2f)",
        path,
        len(df),
        avg_score,
    )

    fonte_counts = (
        df["fonte"].value_counts().to_dict() if "fonte" in df.columns else {}
    )
    logger.info("Distribuição por fonte: %s", fonte_counts)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Agencies ingest job — Datafolha, Quaest, Ipespe, Paraná, CNT/MDA → Bronze"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=int(os.environ.get("AGENCIES_YEAR", "2026")),
        help="Ano eleitoral",
    )
    parser.add_argument(
        "--cargo",
        default=os.environ.get("AGENCIES_CARGO", "presidente"),
        help="Cargo (presidente, governador, senador)",
    )
    args = parser.parse_args()
    main(args.year, args.cargo)
