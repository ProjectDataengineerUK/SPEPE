"""Cloud Run Job: Meta Ad Library → Bronze layer.

Meta Ad Library:
  - Anúncios políticos públicos por lei de transparência
  - Gasto × impressões × distribuição regional (UF) × demográfica
  - Arquivos Bronze separados: ads, regions, demographics

Google Trends moved to social_ingest_job.
"""

from __future__ import annotations

import logging
import os

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.digital_client import (
    fetch_meta_ads,
    get_meta_app_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.digital_ingest")

_CANDIDATOS_BY_YEAR: dict[int, list[str]] = {
    2018: ["Lula", "Jair Bolsonaro", "Ciro Gomes", "Geraldo Alckmin"],
    2022: ["Lula", "Jair Bolsonaro", "Ciro Gomes", "Simone Tebet"],
    2026: [
        "Lula",
        "Tarcísio de Freitas",
        "Jair Bolsonaro",
        "Ciro Gomes",
        "Simone Tebet",
        "Geraldo Alckmin",
        "Fernando Haddad",
        "Guilherme Boulos",
        "Rodrigo Pacheco",
    ],
}


def _write(df: pd.DataFrame, year: int, filename: str) -> None:
    if df.empty:
        logger.info("Skipping empty: %s", filename)
        return
    write_bronze(
        df=df,
        source="digital",
        year=year,
        uf="BR",
        filename=filename,
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )
    logger.info("Digital Bronze: %d registros → %s", len(df), filename)


def ingest_meta_ads(year: int) -> None:
    token = get_meta_app_token()
    if not token:
        logger.warning("META_APP_TOKEN não configurado — Meta Ads pulado.")
        return

    candidatos = _CANDIDATOS_BY_YEAR.get(year, _CANDIDATOS_BY_YEAR[2026])
    logger.info("Meta Ad Library: %d candidatos, ano=%d", len(candidatos), year)

    ads_df, regions_df, demo_df = fetch_meta_ads(
        candidatos=candidatos,
        access_token=token,
        year=year,
        country="BR",
        max_per_candidato=1000,
    )

    if not ads_df.empty:
        ads_df["ingested_at"] = pd.Timestamp.utcnow().isoformat()
        _write(ads_df, year, f"meta_ads_{year}.parquet")

    if not regions_df.empty:
        regions_df["ingested_at"] = pd.Timestamp.utcnow().isoformat()
        _write(regions_df, year, f"meta_ads_regioes_{year}.parquet")

    if not demo_df.empty:
        demo_df["ingested_at"] = pd.Timestamp.utcnow().isoformat()
        _write(demo_df, year, f"meta_ads_demograficos_{year}.parquet")


def main(year: int) -> None:
    logger.info("Digital ingest job: ano=%d", year)
    ingest_meta_ads(year)
    logger.info("Digital ingest concluído: ano=%d", year)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SPEPE Digital Ingest Job")
    parser.add_argument(
        "--year",
        type=int,
        default=int(os.environ.get("DEFAULT_ANO", "2026")),
        help="Ano eleitoral (2018, 2022, 2026)",
    )
    args = parser.parse_args()
    main(year=args.year)
