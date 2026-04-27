"""Cloud Run Job: Twitter/X + Facebook → Bronze layer."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.social_client import (
    aggregate_x_sentiment,
    fetch_fb_page_posts,
    fetch_x_mentions,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.social_ingest")

_DEFAULT_CANDIDATOS = [
    "Lula",
    "Bolsonaro",
    "Lula da Silva",
    "Jair Bolsonaro",
]

_DEFAULT_FB_PAGES: list[str] = []  # Preencher com IDs de páginas quando disponível


def main(candidatos: list[str], fb_pages: list[str], dias: int, year: int) -> None:
    logger.info("Social ingest job: %d candidatos, %d dias", len(candidatos), dias)
    cache_dir = Path("data/bronze/social")
    cache_dir.mkdir(parents=True, exist_ok=True)

    use_gcs = bool(os.environ.get("GCS_BUCKET"))

    # ── Twitter/X ──────────────────────────────────────────────────────────
    mentions = fetch_x_mentions(candidatos, dias=dias, max_por_candidato=500)
    if mentions:
        df_x = pd.DataFrame(mentions)
        write_bronze(
            df=df_x,
            source="social",
            year=year,
            uf="BR",
            filename=f"twitter_mencoes_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info("Twitter/X Bronze: %d menções", len(df_x))

        sentimento = aggregate_x_sentiment(mentions)
        df_sent = pd.DataFrame(sentimento)
        write_bronze(
            df=df_sent,
            source="social",
            year=year,
            uf="BR",
            filename=f"twitter_sentimento_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info("Twitter/X sentimento agregado: %d candidatos", len(df_sent))
    else:
        logger.warning("Twitter/X: nenhuma menção coletada (token ausente ou API vazia)")

    # ── Facebook ───────────────────────────────────────────────────────────
    if fb_pages:
        posts = fetch_fb_page_posts(fb_pages, dias=dias)
        if posts:
            df_fb = pd.DataFrame(posts)
            write_bronze(
                df=df_fb,
                source="social",
                year=year,
                uf="BR",
                filename=f"facebook_posts_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("Facebook Bronze: %d posts", len(df_fb))
    else:
        logger.info("Facebook: nenhuma página configurada — pulando")

    logger.info("Social ingest concluído")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Social ingest job")
    parser.add_argument(
        "--candidatos",
        default=os.environ.get("SOCIAL_CANDIDATOS", json.dumps(_DEFAULT_CANDIDATOS)),
        help="JSON list de nomes de candidatos",
    )
    parser.add_argument(
        "--fb-pages",
        default=os.environ.get("SOCIAL_FB_PAGES", json.dumps(_DEFAULT_FB_PAGES)),
        help="JSON list de IDs de páginas Facebook",
    )
    parser.add_argument("--dias", type=int, default=int(os.environ.get("SOCIAL_DIAS", "7")))
    parser.add_argument("--year", type=int, default=int(os.environ.get("DEFAULT_ANO", "2026")))
    args = parser.parse_args()

    try:
        candidatos = json.loads(args.candidatos)
        fb_pages = json.loads(args.fb_pages)
    except json.JSONDecodeError as exc:
        logger.error("Erro ao parsear argumentos JSON: %s", exc)
        sys.exit(1)

    main(candidatos, fb_pages, args.dias, args.year)
