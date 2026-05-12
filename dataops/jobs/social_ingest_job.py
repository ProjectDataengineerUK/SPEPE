"""Cloud Run Job: social media + imprensa → Bronze layer.

Fontes coletadas:
  Twitter/X      — X_BEARER_TOKEN (opcional)
  Facebook       — META_APP_TOKEN (opcional, pages públicas)
  Instagram      — META_APP_TOKEN (opcional, business accounts)
  YouTube        — YOUTUBE_API_KEY (opcional)
  Bluesky        — sem credenciais (AT Protocol público)
  GDELT          — sem credenciais (imprensa BR indexada); GDELT_ENABLED controla ativação
  RSS portais BR — sem credenciais (G1, Folha, Poder360, etc.)
  Reddit         — REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET (opcional, r/brasil, r/politica, etc.)
  Google Trends  — sem credenciais (pytrends)

SOURCE_FILTER env var: comma-separated list of sources to run. If unset, all sources run.
  E.g.: SOURCE_FILTER=twitter,facebook,youtube

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
from dataops.clients.digital_client import fetch_trends, fetch_trends_by_uf
from dataops.clients.gdelt_client import fetch_gdelt_events
from dataops.clients.news_rss_client import fetch_rss_mentions
from dataops.clients.reddit_client import fetch_reddit_mentions
from dataops.clients.social_client import (
    aggregate_x_sentiment,
    enrich_sentiment_vertex,
    fetch_fb_page_posts,
    fetch_instagram_posts,
    fetch_x_mentions,
    fetch_youtube_videos,
    load_candidate_pages_from_bq,
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

_TIMEFRAME_BY_YEAR: dict[int, str] = {
    2018: "2018-01-01 2018-10-31",
    2022: "2022-01-01 2022-10-31",
    2026: "2026-01-01 2026-12-31",
}


def _source_filter() -> set[str] | None:
    raw = os.environ.get("SOURCE_FILTER", "")
    if not raw:
        return None
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def should_run(source_name: str, filter_set: set[str] | None) -> bool:
    return filter_set is None or source_name in filter_set


def _write(df: pd.DataFrame, source: str, year: int, filename: str, use_gcs: bool) -> None:
    if df.empty:
        logger.info("Skipping empty dataframe: %s", filename)
        return
    write_bronze(df=df, source=source, year=year, uf="BR", filename=filename, use_gcs=use_gcs)
    logger.info("%s Bronze: %d registros → %s", source.upper(), len(df), filename)


def main(candidatos: list[str], dias: int, year: int) -> None:
    logger.info("Social ingest job: %d candidatos, %d dias, year=%d", len(candidatos), dias, year)
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    gcp_project = os.environ.get("GCP_PROJECT_ID", "")
    filter_set = _source_filter()

    Path("data/bronze/social").mkdir(parents=True, exist_ok=True)

    pages: dict[str, list[str]] = {"facebook": [], "instagram": [], "youtube": []}
    if gcp_project:
        pages = load_candidate_pages_from_bq(gcp_project)
        logger.info(
            "dim_candidato_social_pages: %d FB / %d IG / %d YT",
            len(pages["facebook"]),
            len(pages["instagram"]),
            len(pages["youtube"]),
        )

    # ── Twitter/X ─────────────────────────────────────────────────────────────
    if should_run("twitter", filter_set):
        mentions = fetch_x_mentions(candidatos, dias=dias, max_por_candidato=500)
        if mentions:
            if gcp_project:
                mentions = enrich_sentiment_vertex(mentions, text_field="text", project=gcp_project)
            enrich_with_source_meta(mentions)
            _write(
                pd.DataFrame(mentions), "social", year, f"twitter_mencoes_{year}.parquet", use_gcs
            )

            sentimento = aggregate_x_sentiment(mentions)
            _write(
                pd.DataFrame(sentimento),
                "social",
                year,
                f"twitter_sentimento_{year}.parquet",
                use_gcs,
            )
        else:
            logger.warning("Twitter/X: nenhuma menção (token ausente ou API vazia)")

    # ── Facebook ──────────────────────────────────────────────────────────────
    if should_run("facebook", filter_set):
        fb_pages = pages["facebook"]
        if fb_pages:
            posts = fetch_fb_page_posts(fb_pages, dias=dias)
            if posts:
                enrich_with_source_meta(posts)
                _write(
                    pd.DataFrame(posts), "social", year, f"facebook_posts_{year}.parquet", use_gcs
                )
        else:
            logger.info("Facebook: nenhuma página em dim_candidato_social_pages — pulando")

    # ── Instagram ─────────────────────────────────────────────────────────────
    if should_run("instagram", filter_set):
        ig_handles = pages["instagram"]
        if ig_handles:
            ig_dfs: list[pd.DataFrame] = []
            for handle in ig_handles:
                ig_df = fetch_instagram_posts(handle, days_back=dias)
                if not ig_df.empty:
                    ig_df["candidato"] = handle
                    ig_dfs.append(ig_df)
            if ig_dfs:
                combined_ig = pd.concat(ig_dfs, ignore_index=True)
                enrich_with_source_meta(combined_ig.to_dict("records"))
                _write(combined_ig, "social", year, f"instagram_posts_{year}.parquet", use_gcs)
        else:
            logger.info("Instagram: nenhum handle em dim_candidato_social_pages — pulando")

    # ── YouTube ───────────────────────────────────────────────────────────────
    if should_run("youtube", filter_set):
        yt_candidatos = pages["youtube"] if pages["youtube"] else candidatos
        yt_records = fetch_youtube_videos(yt_candidatos, year=year, max_por_candidato=200)
        if yt_records:
            if gcp_project:
                yt_records = enrich_sentiment_vertex(
                    yt_records, text_field="title", project=gcp_project
                )
            enrich_with_source_meta(yt_records)
            _write(
                pd.DataFrame(yt_records), "social", year, f"youtube_videos_{year}.parquet", use_gcs
            )
        else:
            logger.info("YouTube: nenhum vídeo (chave ausente ou API vazia)")

    # ── Bluesky (sem credenciais) ─────────────────────────────────────────────
    if should_run("bluesky", filter_set):
        bsky_posts = fetch_bluesky_mentions(candidatos, dias=dias, max_por_candidato=100)
        if bsky_posts:
            enrich_with_source_meta(bsky_posts)
            _write(
                pd.DataFrame(bsky_posts), "social", year, f"bluesky_posts_{year}.parquet", use_gcs
            )
        else:
            logger.info("Bluesky: nenhum post coletado")

    # ── GDELT (GDELT_ENABLED controla ativação) ───────────────────────────────
    if should_run("gdelt", filter_set):
        gdelt_df = fetch_gdelt_events(candidatos, dias=dias)
        if not gdelt_df.empty:
            enrich_with_source_meta(gdelt_df.to_dict("records"))
            _write(gdelt_df, "social", year, f"gdelt_noticias_{year}.parquet", use_gcs)

    # ── RSS portais BR (sem credenciais) ─────────────────────────────────────
    if should_run("rss", filter_set):
        rss_articles = fetch_rss_mentions(candidatos)
        if rss_articles:
            enrich_with_source_meta(rss_articles, fonte_field="fonte_especifica")
            _write(
                pd.DataFrame(rss_articles), "social", year, f"rss_noticias_{year}.parquet", use_gcs
            )
        else:
            logger.info("RSS: nenhum artigo coletado")

    # ── Reddit (r/brasil, r/politica, etc.) ───────────────────────────────────
    if should_run("reddit", filter_set):
        reddit_posts = fetch_reddit_mentions(candidatos, dias=dias, max_por_candidato=300)
        if reddit_posts:
            if gcp_project:
                reddit_posts = enrich_sentiment_vertex(
                    reddit_posts, text_field="text", project=gcp_project
                )
            enrich_with_source_meta(reddit_posts)
            _write(
                pd.DataFrame(reddit_posts), "social", year, f"reddit_posts_{year}.parquet", use_gcs
            )
        else:
            logger.warning("Reddit: nenhum post (credenciais ausentes ou API vazia)")

    # ── Google Trends ─────────────────────────────────────────────────────────
    if should_run("google_trends", filter_set):
        timeframe = _TIMEFRAME_BY_YEAR.get(year, "today 12-m")
        df_timeline = fetch_trends(candidatos, timeframe=timeframe, geo="BR")
        if not df_timeline.empty:
            df_reset = df_timeline.reset_index()
            df_reset["ano"] = year
            df_reset["fonte"] = "google_trends"
            _write(df_reset, "social", year, f"google_trends_timeline_{year}.parquet", use_gcs)

        df_uf = fetch_trends_by_uf(candidatos, timeframe=timeframe)
        if not df_uf.empty:
            df_uf["ano"] = year
            _write(df_uf, "social", year, f"google_trends_por_uf_{year}.parquet", use_gcs)

    logger.info("Social ingest concluído")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Social ingest job")
    parser.add_argument(
        "--candidatos",
        default=os.environ.get("SOCIAL_CANDIDATOS", json.dumps(_DEFAULT_CANDIDATOS)),
    )
    parser.add_argument("--dias", type=int, default=int(os.environ.get("SOCIAL_DIAS", "7")))
    parser.add_argument("--year", type=int, default=int(os.environ.get("DEFAULT_ANO", "2026")))
    args = parser.parse_args()

    try:
        candidatos = json.loads(args.candidatos)
    except json.JSONDecodeError as exc:
        logger.error("Erro ao parsear --candidatos JSON: %s", exc)
        sys.exit(1)

    main(candidatos, args.dias, args.year)
