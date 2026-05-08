"""Cloud Run Job: monthly update of dim_candidato_social_pages from fact_pesquisa + Facebook Graph API."""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd
import requests

from security.secret_manager import get_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.candidatos_discovery")

_FB_SEARCH_URL = "https://graph.facebook.com/v19.0/search"

QUERY_CANDIDATOS = """
    SELECT nm_candidato AS candidato, SUM(total_votos) AS mencoes
    FROM `{project}.spepe_gold.fact_candidato_eleicao`
    WHERE ano_eleicao >= 2022
    GROUP BY nm_candidato
    ORDER BY mencoes DESC
    LIMIT 150
"""

MERGE_SQL = """
MERGE `{project}.spepe_silver.dim_candidato_social_pages` AS T
USING (
    SELECT
        nome_candidato AS candidato_id,
        nome_candidato,
        facebook_page_id,
        facebook_page_name,
        CAST(followers_fb AS INT64) AS followers_fb,
        is_verified,
        CURRENT_TIMESTAMP() AS updated_at
    FROM UNNEST(@rows)
) AS S
ON T.candidato_id = S.candidato_id
WHEN MATCHED THEN UPDATE SET
    facebook_page_id   = S.facebook_page_id,
    facebook_page_name = S.facebook_page_name,
    followers_fb       = S.followers_fb,
    is_verified        = S.is_verified,
    updated_at         = S.updated_at
WHEN NOT MATCHED THEN INSERT (
    candidato_id, nome_candidato, facebook_page_id, facebook_page_name,
    followers_fb, is_verified, instagram_handle, youtube_channel_id,
    twitter_handle, tiktok_handle, updated_at
) VALUES (
    S.candidato_id, S.nome_candidato, S.facebook_page_id, S.facebook_page_name,
    S.followers_fb, S.is_verified, NULL, NULL, NULL, NULL, S.updated_at
)
"""


def _get_fb_token() -> str:
    return get_secret("META_APP_TOKEN") or os.environ.get("META_APP_TOKEN", "")


def search_facebook_page(nome: str, token: str) -> dict | None:
    params = {
        "q": nome,
        "type": "page",
        "fields": "id,name,fan_count,verification_status,about",
        "access_token": token,
        "limit": 5,
    }
    try:
        resp = requests.get(_FB_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None

        def score(page: dict) -> int:
            s = 100 if page.get("verification_status") == "blue_verified" else 0
            s += min(int(page.get("fan_count", 0) ** 0.3), 50)
            about = (page.get("about") or "").lower()
            s += 20 if any(
                kw in about
                for kw in ["candidato", "deputado", "senador", "governador", "prefeito", "vereador"]
            ) else 0
            return s

        return max(data, key=score)
    except Exception as exc:
        logger.warning("FB search failed for '%s': %s", nome, exc)
        return None


def main() -> None:
    project_id = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    logger.info("Candidatos discovery job: project=%s", project_id)

    token = _get_fb_token()
    if not token:
        logger.error("META_APP_TOKEN not configured — aborting")
        sys.exit(1)

    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery not installed")
        sys.exit(1)

    bq_client = bigquery.Client(project=project_id)

    df_candidatos = bq_client.query(QUERY_CANDIDATOS.format(project=project_id)).to_dataframe()
    logger.info("fact_pesquisa: %d candidates with >=3 mentions in 2026", len(df_candidatos))

    if df_candidatos.empty:
        logger.info("No candidates found — nothing to upsert")
        return

    pages_found = 0
    rows: list[dict] = []
    for _, row in df_candidatos.iterrows():
        page = search_facebook_page(row["candidato"], token)
        if page:
            pages_found += 1
        rows.append(
            {
                "nome_candidato": row["candidato"],
                "facebook_page_id": page["id"] if page else None,
                "facebook_page_name": page.get("name") if page else None,
                "followers_fb": int(page.get("fan_count", 0)) if page else 0,
                "is_verified": (
                    page.get("verification_status") == "blue_verified" if page else False
                ),
            }
        )

    logger.info(
        "Facebook search: %d candidates processed, %d pages found",
        len(rows),
        pages_found,
    )

    df_rows = pd.DataFrame(rows)
    table_ref = f"{project_id}.spepe_silver.dim_candidato_social_pages"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema=[
            bigquery.SchemaField("candidato_id", "STRING"),
            bigquery.SchemaField("nome_candidato", "STRING"),
            bigquery.SchemaField("facebook_page_id", "STRING"),
            bigquery.SchemaField("facebook_page_name", "STRING"),
            bigquery.SchemaField("followers_fb", "INTEGER"),
            bigquery.SchemaField("is_verified", "BOOLEAN"),
            bigquery.SchemaField("instagram_handle", "STRING"),
            bigquery.SchemaField("youtube_channel_id", "STRING"),
            bigquery.SchemaField("twitter_handle", "STRING"),
            bigquery.SchemaField("tiktok_handle", "STRING"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
    )

    df_rows["candidato_id"] = df_rows["nome_candidato"]
    df_rows["instagram_handle"] = None
    df_rows["youtube_channel_id"] = None
    df_rows["twitter_handle"] = None
    df_rows["tiktok_handle"] = None
    df_rows["updated_at"] = pd.Timestamp.utcnow()

    try:
        load_job = bq_client.load_table_from_dataframe(df_rows, table_ref, job_config=job_config)
        load_job.result()
        logger.info(
            "dim_candidato_social_pages: %d rows upserted to %s", len(df_rows), table_ref
        )
    except Exception as exc:
        logger.error("BQ load failed: %s", exc)
        sys.exit(1)

    logger.info("Candidatos discovery job concluído")


if __name__ == "__main__":
    main()
