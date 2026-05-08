"""
Usage: python scripts/discover_candidate_pages.py --project spepe-prod --output candidates.csv
"""
import argparse
import logging
import os

import pandas as pd
import requests
from google.cloud import bigquery

logger = logging.getLogger(__name__)

QUERY_CANDIDATOS = """
    SELECT nm_candidato AS candidato, SUM(total_votos) AS mencoes
    FROM `{project}.spepe_gold.fact_candidato_eleicao`
    WHERE ano_eleicao >= 2022
    GROUP BY nm_candidato
    ORDER BY mencoes DESC
    LIMIT 150
"""


def search_facebook_page(nome: str, token: str) -> dict | None:
    url = "https://graph.facebook.com/v19.0/search"
    params = {
        "q": nome,
        "type": "page",
        "fields": "id,name,fan_count,verification_status,about",
        "access_token": token,
        "limit": 5,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
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
    except Exception as e:
        logger.warning("FB search failed for %s: %s", nome, e)
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", "spepe-prod"))
    parser.add_argument("--output", default="candidates_discovery.csv")
    args = parser.parse_args()

    token = os.environ.get("META_APP_TOKEN", "")
    if not token:
        logger.error("META_APP_TOKEN not set")
        return

    client = bigquery.Client(project=args.project)
    df_candidatos = client.query(QUERY_CANDIDATOS.format(project=args.project)).to_dataframe()
    logger.info("Found %d candidates with >=3 mentions in 2026 polls", len(df_candidatos))

    results = []
    for _, row in df_candidatos.iterrows():
        page = search_facebook_page(row["candidato"], token)
        results.append(
            {
                "nome_candidato": row["candidato"],
                "mencoes_pesquisa": row["mencoes"],
                "facebook_page_id": page["id"] if page else None,
                "facebook_page_name": page.get("name") if page else None,
                "followers_fb": page.get("fan_count", 0) if page else 0,
                "is_verified": (
                    page.get("verification_status") == "blue_verified" if page else False
                ),
                "instagram_handle": None,
                "youtube_channel_id": None,
                "twitter_handle": None,
                "tiktok_handle": None,
                "aprovado": "",
            }
        )

    df_out = pd.DataFrame(results)
    df_out.to_csv(args.output, index=False)
    logger.info(
        "Output: %s — review and set 'aprovado=TRUE' before loading to BQ", args.output
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
