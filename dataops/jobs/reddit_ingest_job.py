"""Cloud Run Job: Reddit (r/brasil, r/politica) → Bronze layer."""

from __future__ import annotations

import json
import logging
import os
import sys

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.reddit_client import fetch_reddit_mentions, fetch_reddit_subreddit_new
from dataops.clients.social_client import enrich_sentiment_vertex

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.reddit_ingest")

_DEFAULT_CANDIDATOS = [
    "Lula", "Lula da Silva",
    "Tarcísio de Freitas", "Tarcísio Freitas",
    "Bolsonaro", "Jair Bolsonaro",
    "Simone Tebet", "Ciro Gomes",
    "Alckmin", "Geraldo Alckmin",
    "Rodrigo Pacheco",
    "Fernando Haddad", "Guilherme Boulos",
]

_DEFAULT_SUBREDDITS = ["brasil", "politica", "brasilivre"]


def main(candidatos: list[str], subreddits: list[str], year: int, dias: int) -> None:
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    gcp_project = os.environ.get("GCP_PROJECT_ID", "")

    # ── Menções por candidato ───────────────────────────────────────────────
    mentions = fetch_reddit_mentions(
        candidatos=candidatos,
        subreddits=subreddits,
        max_por_candidato=300,
        dias=dias,
    )
    if mentions:
        if gcp_project:
            mentions = enrich_sentiment_vertex(mentions, text_field="text", project=gcp_project)
        df = pd.DataFrame(mentions)
        write_bronze(
            df=df,
            source="social",
            year=year,
            uf="BR",
            filename=f"reddit_mencoes_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info("Reddit Bronze: %d posts de menções", len(df))
    else:
        logger.warning("Reddit: nenhuma menção coletada (credenciais ausentes ou API vazia)")

    # ── Posts recentes dos subreddits (sem filtro de candidato) ────────────
    for sub in subreddits:
        posts = fetch_reddit_subreddit_new(sub, limit=500)
        if posts:
            if gcp_project:
                posts = enrich_sentiment_vertex(posts, text_field="text", project=gcp_project)
            df_sub = pd.DataFrame(posts)
            write_bronze(
                df=df_sub,
                source="social",
                year=year,
                uf="BR",
                filename=f"reddit_{sub}_new_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("Reddit /r/%s: %d posts recentes", sub, len(df_sub))

    logger.info("Reddit ingest concluído")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidatos",
        default=os.environ.get("REDDIT_CANDIDATOS", json.dumps(_DEFAULT_CANDIDATOS)),
    )
    parser.add_argument(
        "--subreddits",
        default=os.environ.get("REDDIT_SUBREDDITS", json.dumps(_DEFAULT_SUBREDDITS)),
    )
    parser.add_argument("--year", type=int, default=int(os.environ.get("DEFAULT_ANO", "2026")))
    parser.add_argument("--dias", type=int, default=int(os.environ.get("REDDIT_DIAS", "30")))
    args = parser.parse_args()

    try:
        candidatos = json.loads(args.candidatos)
        subreddits = json.loads(args.subreddits)
    except json.JSONDecodeError as exc:
        logger.error("Erro JSON args: %s", exc)
        sys.exit(1)

    main(candidatos, subreddits, args.year, args.dias)
