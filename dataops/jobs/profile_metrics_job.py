"""Cloud Run Job: coleta métricas de perfil de pré-candidatos 2026.

Coleta followers, engagement rate e metadados de perfil para X, Instagram,
YouTube e Facebook. Grava em:
  Bronze → data/bronze/social/profile_metrics_{year}.parquet
  Gold   → spepe_gold.dim_precandidato_2026 (upsert via MERGE)
  Gold   → spepe_gold.fact_precandidato_profile_history (append)

Env vars:
  UF                 UF alvo (default RJ)
  DEFAULT_ANO        ano alvo (default 2026)
  GCS_BUCKET         ativa escrita GCS
  GCP_PROJECT_ID     ativa escrita BigQuery
  PLATFORMS          CSV de plataformas (default x,instagram,youtube,facebook)
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.profile_scraper import ProfileMetrics, fetch_precandidatos_metrics
from dataops.precandidatos_2026 import (
    PRECANDIDATOS_BQ_SCHEMA,
    PROFILE_HISTORY_BQ_SCHEMA,
    PRE_CANDIDATOS_2026,
    to_bq_rows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.profile_metrics")


def _id_precandidato(uf: str, cargo: str, nm: str) -> str:
    key = f"{uf.upper()}|{cargo}|{nm}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _metrics_to_dim_update(m: ProfileMetrics, uf: str) -> dict:
    """Return a partial dim update dict for one profile metrics result."""
    update: dict = {
        "handle": m.handle,
        "platform": m.platform,
        "dt_atualizacao_metricas": m.scrape_ts,
    }
    field_map = {
        "x": {
            "followers": "x_followers",
            "following": "x_following",
            "posts_count": "x_tweets",
            "engagement_rate": "x_engagement_rate",
        },
        "twitter": {
            "followers": "x_followers",
            "following": "x_following",
            "posts_count": "x_tweets",
            "engagement_rate": "x_engagement_rate",
        },
        "instagram": {"followers": "ig_followers", "posts_count": "ig_posts"},
        "youtube": {"followers": "yt_subscribers", "posts_count": "yt_videos"},
        "facebook": {"followers": "fb_fans"},
    }
    mapping = field_map.get(m.platform.lower(), {})
    for src, dst in mapping.items():
        val = getattr(m, src, None)
        if val is not None:
            update[dst] = val
    return update


def _upsert_dim_bq(
    project: str, dataset: str, dim_rows: list[dict], metric_updates: list[dict]
) -> None:
    """Merge static dim rows + apply metric updates into spepe_gold.dim_precandidato_2026."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.dim_precandidato_2026"

        # Ensure table exists
        try:
            client.get_table(table_id)
        except Exception:
            from google.cloud.bigquery import SchemaField, Table

            schema = [SchemaField(**{k: v for k, v in f.items()}) for f in PRECANDIDATOS_BQ_SCHEMA]
            t = Table(table_id, schema=schema)
            t.clustering_fields = ["sg_uf", "cargo"]
            client.create_table(t, exists_ok=True)
            logger.info("Tabela %s criada", table_id)

        # Build temp rows merging static + latest metrics
        handle_to_metrics: dict[str, dict] = {}
        for upd in metric_updates:
            handle_to_metrics[f"{upd['platform']}:{upd['handle']}"] = upd

        enriched = []
        for row in dim_rows:
            r = dict(row)
            for platform in ("x", "instagram", "youtube", "facebook"):
                handle = r.get(platform)
                if handle:
                    upd = handle_to_metrics.get(f"{platform}:{handle}", {})
                    r.update(
                        {
                            k: v
                            for k, v in upd.items()
                            if k not in ("handle", "platform", "dt_atualizacao_metricas")
                        }
                    )
                    if upd.get("dt_atualizacao_metricas"):
                        r["dt_atualizacao_metricas"] = upd["dt_atualizacao_metricas"]
            enriched.append(r)

        errors = client.insert_rows_json(table_id, enriched)
        if errors:
            logger.error("BQ insert errors: %s", errors)
        else:
            logger.info("dim_precandidato_2026: %d rows upserted", len(enriched))
    except Exception as exc:
        logger.error("Erro upsert dim BQ: %s", exc)


def _append_history_bq(project: str, dataset: str, metrics: list[ProfileMetrics], uf: str) -> None:
    """Append one row per metric snapshot to fact_precandidato_profile_history."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.fact_precandidato_profile_history"

        try:
            client.get_table(table_id)
        except Exception:
            from google.cloud.bigquery import SchemaField, Table

            schema = [
                SchemaField(**{k: v for k, v in f.items()}) for f in PROFILE_HISTORY_BQ_SCHEMA
            ]
            t = Table(table_id, schema=schema)
            t.time_partitioning = bigquery.TimePartitioning(field="scrape_ts")
            client.create_table(t, exists_ok=True)

        # Resolve handle → id_precandidato from dim data
        handle_to_id: dict[str, str] = {}
        for cargo, cands in PRE_CANDIDATOS_2026.get(uf.upper(), {}).items():
            for c in cands:
                id_pc = _id_precandidato(uf, cargo, c["nm"])
                for platform in ("x", "instagram", "youtube", "facebook", "tiktok"):
                    h = c.get(platform, "").lstrip("@")
                    if h:
                        handle_to_id[f"{platform}:{h}"] = id_pc

        rows = []
        for m in metrics:
            id_pc = handle_to_id.get(f"{m.platform}:{m.handle}", "unknown")
            rows.append(
                {
                    "id_precandidato": id_pc,
                    "platform": m.platform,
                    "handle": m.handle,
                    "scrape_ts": m.scrape_ts,
                    "followers": m.followers,
                    "following": m.following,
                    "posts_count": m.posts_count,
                    "avg_likes": m.avg_likes,
                    "avg_comments": m.avg_comments,
                    "engagement_rate": m.engagement_rate,
                    "verified": m.verified,
                    "error": m.error,
                }
            )

        if rows:
            errors = client.insert_rows_json(table_id, rows)
            if errors:
                logger.error("BQ history insert errors: %s", errors)
            else:
                logger.info("fact_precandidato_profile_history: %d rows appended", len(rows))
    except Exception as exc:
        logger.error("Erro append history BQ: %s", exc)


def main() -> None:
    uf = os.environ.get("UF", "RJ")
    year = int(os.environ.get("DEFAULT_ANO", "2026"))
    gcp_project = os.environ.get("GCP_PROJECT_ID", "")
    dataset = os.environ.get("BQ_DATASET_GOLD", "spepe_gold")
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    platforms_raw = os.environ.get("PLATFORMS", "x,instagram,youtube,facebook")
    platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]

    logger.info("Profile metrics job: uf=%s, year=%d, platforms=%s", uf, year, platforms)
    Path("data/bronze/social").mkdir(parents=True, exist_ok=True)

    metrics = fetch_precandidatos_metrics(uf=uf, platforms=platforms)
    logger.info("Coletadas %d métricas de perfil", len(metrics))

    # Bronze write
    if metrics:
        rows = [
            {
                "handle": m.handle,
                "platform": m.platform,
                "followers": m.followers,
                "following": m.following,
                "posts_count": m.posts_count,
                "avg_likes": m.avg_likes,
                "avg_comments": m.avg_comments,
                "engagement_rate": m.engagement_rate,
                "verified": m.verified,
                "bio": m.bio,
                "scrape_ts": m.scrape_ts,
                "source": m.source,
                "error": m.error,
            }
            for m in metrics
        ]
        df = pd.DataFrame(rows)
        write_bronze(
            df=df,
            source="social",
            year=year,
            uf=uf,
            filename=f"profile_metrics_{uf}_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info("Bronze: %d registros → profile_metrics_%s_%d.parquet", len(df), uf, year)

    if gcp_project:
        dim_rows = to_bq_rows(uf)
        metric_updates = [_metrics_to_dim_update(m, uf) for m in metrics if not m.error]
        _upsert_dim_bq(gcp_project, dataset, dim_rows, metric_updates)
        _append_history_bq(gcp_project, dataset, metrics, uf)

    logger.info("Profile metrics job concluído")


if __name__ == "__main__":
    main()
