"""Cloud Run Job: Google Trends + Meta Ad Library → Bronze layer.

Meta Ad Library:
  - Anúncios políticos públicos por lei de transparência
  - Gasto × impressões × distribuição regional (UF) × demográfica
  - Arquivos Bronze separados: ads, regions, demographics

Google Trends:
  - Interesse de busca relativo (0-100) nacional e por UF
  - Arquivos Bronze separados: timeline e por_uf
"""

from __future__ import annotations

import logging
import os

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.digital_client import (
    fetch_meta_ads,
    fetch_trends,
    fetch_trends_by_uf,
    get_meta_app_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.digital_ingest")

# Candidatos por eleição — mantém histórico para análise de série temporal
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

_TIMEFRAME_BY_YEAR: dict[int, str] = {
    2018: "2018-01-01 2018-10-31",
    2022: "2022-01-01 2022-10-31",
    2026: "2026-01-01 2026-12-31",
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


def ingest_trends(year: int) -> None:
    candidatos = _CANDIDATOS_BY_YEAR.get(year, _CANDIDATOS_BY_YEAR[2026])
    timeframe = _TIMEFRAME_BY_YEAR.get(year, "today 12-m")
    logger.info("Google Trends: timeline + UF, ano=%d", year)

    # Timeline nacional
    df_timeline = fetch_trends(candidatos, timeframe=timeframe, geo="BR")
    if not df_timeline.empty:
        df_reset = df_timeline.reset_index()
        df_reset["ano"] = year
        df_reset["fonte"] = "google_trends"
        _write(df_reset, year, f"google_trends_timeline_{year}.parquet")

    # Por UF
    df_uf = fetch_trends_by_uf(candidatos, timeframe=timeframe)
    if not df_uf.empty:
        df_uf["ano"] = year
        _write(df_uf, year, f"google_trends_por_uf_{year}.parquet")


def main(year: int) -> None:
    logger.info("Digital ingest job: ano=%d", year)
    ingest_meta_ads(year)
    ingest_trends(year)
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
