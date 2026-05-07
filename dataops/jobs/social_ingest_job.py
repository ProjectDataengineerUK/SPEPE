"""Cloud Run Job: social media + imprensa → Bronze layer.

Fontes coletadas:
  Twitter/X      — X_BEARER_TOKEN (opcional)
  Facebook       — META_APP_TOKEN (opcional, pages públicas)
  YouTube        — YOUTUBE_API_KEY (opcional)
  Bluesky        — sem credenciais (AT Protocol público)
  GDELT          — sem credenciais (imprensa BR indexada)
  RSS portais BR — sem credenciais (G1, Folha, Poder360, etc.)

Cada registro recebe score_confiabilidade + tipo_fonte via source_registry.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.bluesky_client import fetch_bluesky_mentions
from dataops.clients.news_rss_client import fetch_rss_mentions
from dataops.clients.social_client import (
    aggregate_x_sentiment,
    enrich_sentiment_vertex,
    fetch_fb_page_posts,
    fetch_x_mentions,
    fetch_youtube_videos,
)
from dataops.source_registry import enrich_with_source_meta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.social_ingest")

_DEFAULT_CANDIDATOS = [
    "Lula",
    "Lula da Silva",
    "Tarcísio de Freitas",
    "Tarcísio Freitas",
    "Bolsonaro",
    "Jair Bolsonaro",
    "Simone Tebet",
    "Ciro Gomes",
    "Alckmin",
    "Geraldo Alckmin",
    "Rodrigo Pacheco",
    "Fernando Haddad",
    "Guilherme Boulos",
]

_DEFAULT_FB_PAGES: list[str] = []


def _write(df: pd.DataFrame, source: str, year: int, filename: str, use_gcs: bool) -> None:
    if df.empty:
        logger.info("Skipping empty dataframe: %s", filename)
        return
    write_bronze(df=df, source=source, year=year, uf="BR", filename=filename, use_gcs=use_gcs)
    logger.info("%s Bronze: %d registros → %s", source.upper(), len(df), filename)


def main(candidatos: list[str], fb_pages: list[str], dias: int, year: int) -> None:
    logger.info("Social ingest job: %d candidatos, %d dias, year=%d", len(candidatos), dias, year)
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    gcp_project = os.environ.get("GCP_PROJECT_ID", "")

    Path("data/bronze/social").mkdir(parents=True, exist_ok=True)

    # ── Twitter/X ─────────────────────────────────────────────────────────────
    mentions = fetch_x_mentions(candidatos, dias=dias, max_por_candidato=500)
    if mentions:
        if gcp_project:
            mentions = enrich_sentiment_vertex(mentions, text_field="text", project=gcp_project)
        enrich_with_source_meta(mentions)
        _write(pd.DataFrame(mentions), "social", year, f"twitter_mencoes_{year}.parquet", use_gcs)

        sentimento = aggregate_x_sentiment(mentions)
        _write(
            pd.DataFrame(sentimento), "social", year, f"twitter_sentimento_{year}.parquet", use_gcs
        )
    else:
        logger.warning("Twitter/X: nenhuma menção (token ausente ou API vazia)")

    # ── Facebook ──────────────────────────────────────────────────────────────
    if fb_pages:
        posts = fetch_fb_page_posts(fb_pages, dias=dias)
        if posts:
            enrich_with_source_meta(posts)
            _write(pd.DataFrame(posts), "social", year, f"facebook_posts_{year}.parquet", use_gcs)
    else:
        logger.info("Facebook: nenhuma página configurada — pulando")

    # ── YouTube ───────────────────────────────────────────────────────────────
    yt_records = fetch_youtube_videos(candidatos, year=year, max_por_candidato=200)
    if yt_records:
        if gcp_project:
            yt_records = enrich_sentiment_vertex(
                yt_records, text_field="title", project=gcp_project
            )
        enrich_with_source_meta(yt_records)
        _write(pd.DataFrame(yt_records), "social", year, f"youtube_videos_{year}.parquet", use_gcs)
    else:
        logger.info("YouTube: nenhum vídeo (chave ausente ou API vazia)")

    # ── Bluesky (sem credenciais) ─────────────────────────────────────────────
    bsky_posts = fetch_bluesky_mentions(candidatos, dias=dias, max_por_candidato=100)
    if bsky_posts:
        enrich_with_source_meta(bsky_posts)
        _write(pd.DataFrame(bsky_posts), "social", year, f"bluesky_posts_{year}.parquet", use_gcs)
    else:
        logger.info("Bluesky: nenhum post coletado")

    # GDELT desabilitado — rate limit severo bloqueia o job inteiro (30s/candidato)
    logger.info("GDELT: desabilitado (rate limit)")

    # ── RSS portais BR (sem credenciais) ─────────────────────────────────────
    rss_articles = fetch_rss_mentions(candidatos)
    if rss_articles:
        enrich_with_source_meta(rss_articles, fonte_field="fonte_especifica")
        _write(pd.DataFrame(rss_articles), "social", year, f"rss_noticias_{year}.parquet", use_gcs)
    else:
        logger.info("RSS: nenhum artigo coletado")

    logger.info("Social ingest concluído")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Social ingest job")
    parser.add_argument(
        "--candidatos",
        default=os.environ.get("SOCIAL_CANDIDATOS", json.dumps(_DEFAULT_CANDIDATOS)),
    )
    parser.add_argument(
        "--fb-pages",
        default=os.environ.get("SOCIAL_FB_PAGES", json.dumps(_DEFAULT_FB_PAGES)),
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
