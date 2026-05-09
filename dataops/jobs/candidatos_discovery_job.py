"""Cloud Run Job: monthly update of dim_candidato_social_pages.

Fluxo:
  1. Busca top 150 candidatos de fact_candidato_eleicao (Gold)
  2. Para cada candidato:
     a. Tenta Facebook via URL direta (username normalizado) — sem App Review
     b. Fallback: Facebook Graph search (requer page_public_content_access)
     c. Tenta YouTube Data API v3 search (requer YOUTUBE_API_KEY)
  3. Escreve em staging table e faz MERGE em dim_candidato_social_pages (Silver)
"""

from __future__ import annotations

import logging
import os
import sys
import unicodedata

import pandas as pd
import requests

from security.secret_manager import get_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.candidatos_discovery")

# ── Endpoints ────────────────────────────────────────────────────────────────

_FB_GRAPH_BASE = "https://graph.facebook.com/v19.0"
_FB_SEARCH_URL = f"{_FB_GRAPH_BASE}/search"

_YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YT_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# ── BigQuery SQL ─────────────────────────────────────────────────────────────

QUERY_CANDIDATOS = """
    SELECT nm_candidato AS candidato, SUM(total_votos) AS total_votos
    FROM `{project}.spepe_gold.fact_candidato_eleicao`
    WHERE ano_eleicao >= 2022
    GROUP BY nm_candidato
    ORDER BY total_votos DESC
    LIMIT 150
"""

# Staging table recebe os dados desta execução; MERGE atualiza a tabela principal
# preservando entradas manuais e dados de outras fontes (instagram, twitter, tiktok).
_STAGING_TABLE = "{project}.spepe_silver._dim_candidato_social_pages_stage"

MERGE_SQL = """
MERGE `{project}.spepe_silver.dim_candidato_social_pages` AS T
USING `{project}.spepe_silver._dim_candidato_social_pages_stage` AS S
ON T.candidato_id = S.candidato_id
WHEN MATCHED THEN UPDATE SET
    facebook_page_id   = COALESCE(S.facebook_page_id, T.facebook_page_id),
    facebook_page_name = COALESCE(S.facebook_page_name, T.facebook_page_name),
    followers_fb       = COALESCE(S.followers_fb, T.followers_fb),
    is_verified        = COALESCE(S.is_verified, T.is_verified),
    youtube_channel_id = COALESCE(S.youtube_channel_id, T.youtube_channel_id),
    updated_at         = S.updated_at
WHEN NOT MATCHED THEN INSERT (
    candidato_id, nome_candidato, facebook_page_id, facebook_page_name,
    followers_fb, is_verified, instagram_handle, youtube_channel_id,
    twitter_handle, tiktok_handle, updated_at
) VALUES (
    S.candidato_id, S.nome_candidato, S.facebook_page_id, S.facebook_page_name,
    S.followers_fb, S.is_verified, NULL, S.youtube_channel_id,
    NULL, NULL, S.updated_at
)
"""

_BQ_SCHEMA = [
    {"name": "candidato_id", "type": "STRING"},
    {"name": "nome_candidato", "type": "STRING"},
    {"name": "facebook_page_id", "type": "STRING"},
    {"name": "facebook_page_name", "type": "STRING"},
    {"name": "followers_fb", "type": "INTEGER"},
    {"name": "is_verified", "type": "BOOLEAN"},
    {"name": "instagram_handle", "type": "STRING"},
    {"name": "youtube_channel_id", "type": "STRING"},
    {"name": "twitter_handle", "type": "STRING"},
    {"name": "tiktok_handle", "type": "STRING"},
    {"name": "updated_at", "type": "TIMESTAMP"},
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_fb_token() -> str:
    return get_secret("META_APP_TOKEN") or os.environ.get("META_APP_TOKEN", "")


def _normalize_username(name: str) -> str:
    """Normaliza nome para tentar como username do Facebook.

    Ex: "Luiz Inácio Lula da Silva" → "luizinacioluladas ilva"
    """
    nfkd = unicodedata.normalize("NFKD", name.lower())
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.replace(" ", "").replace("-", "").replace(".", "")


def _score_fb_page(page: dict, nome: str) -> int:
    """Pontua uma página FB por relevância para o candidato."""
    score = 0
    if page.get("verification_status") == "blue_verified":
        score += 100
    score += min(int(page.get("fan_count", 0) ** 0.3), 50)
    about = (page.get("about") or "").lower()
    political_kws = [
        "candidato",
        "deputado",
        "senador",
        "governador",
        "prefeito",
        "vereador",
        "partido",
    ]
    if any(kw in about for kw in political_kws):
        score += 20
    # Bonus se título da página contém o sobrenome do candidato
    lastname = nome.split()[-1].lower() if nome else ""
    if lastname and lastname in (page.get("name") or "").lower():
        score += 15
    return score


# ── Facebook discovery ────────────────────────────────────────────────────────


def lookup_facebook_direct(nome: str, token: str) -> dict | None:
    """Tenta resolver a página via username normalizado — sem permissão de search.

    Funciona para candidatos cujo username no FB coincide com o nome normalizado.
    Ex: "Lula" → graph.facebook.com/v19.0/lula
    """
    username = _normalize_username(nome)
    if len(username) < 3:
        return None
    try:
        resp = requests.get(
            f"{_FB_GRAPH_BASE}/{username}",
            params={
                "fields": "id,name,fan_count,verification_status,about",
                "access_token": token,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "id" in data and int(data.get("fan_count", 0)) > 500:
                logger.debug(
                    "FB direct hit para '%s' → %s (%d seguidores)",
                    nome,
                    data["name"],
                    data.get("fan_count", 0),
                )
                return data
    except Exception as exc:
        logger.debug("FB direct lookup falhou para '%s': %s", nome, exc)
    return None


def search_facebook_page(nome: str, token: str) -> dict | None:
    """Fallback: busca via Graph Search API (requer page_public_content_access).

    Se retornar erro de permissão (code 200), loga aviso e retorna None sem abortar.
    """
    params = {
        "q": nome,
        "type": "page",
        "fields": "id,name,fan_count,verification_status,about",
        "access_token": token,
        "limit": "5",
    }
    try:
        resp = requests.get(_FB_SEARCH_URL, params=params, timeout=10)
        data = resp.json()

        # Erro de permissão — search bloqueado sem App Review
        if "error" in data:
            err_code = data["error"].get("code", 0)
            if err_code in (200, 10, 190):
                logger.warning(
                    "FB search bloqueado (permissão): %s", data["error"].get("message", "")
                )
                return None
            logger.warning(
                "FB search erro %d para '%s': %s", err_code, nome, data["error"].get("message")
            )
            return None

        pages = data.get("data", [])
        if not pages:
            return None
        return max(pages, key=lambda p: _score_fb_page(p, nome))
    except Exception as exc:
        logger.warning("FB search exception para '%s': %s", nome, exc)
        return None


def find_facebook_page(nome: str, token: str) -> dict | None:
    """Tenta URL direta primeiro, depois search como fallback."""
    page = lookup_facebook_direct(nome, token)
    if page:
        return page
    return search_facebook_page(nome, token)


# ── YouTube discovery ─────────────────────────────────────────────────────────


def discover_youtube_channel(nome: str) -> str | None:
    """Busca canal YouTube do candidato via YouTube Data API v3.

    Quota: search.list = 100 unidades/chamada. Limite diário: 10.000 unidades.
    Para 100 candidatos = 10.000 unidades — no limite do tier gratuito.
    """
    if not _YT_API_KEY:
        return None
    try:
        resp = requests.get(
            _YT_SEARCH_URL,
            params={
                "q": f"{nome} oficial",
                "type": "channel",
                "maxResults": 3,
                "part": "snippet",
                "relevanceLanguage": "pt",
                "regionCode": "BR",
                "key": _YT_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None

        # Score: quantas palavras do nome aparecem no título do canal
        name_words = [w.lower() for w in nome.split() if len(w) > 2]

        def _score(item: dict) -> int:
            title = item["snippet"].get("channelTitle", "").lower()
            return sum(1 for w in name_words if w in title)

        best = max(items, key=_score)
        # Só aceita se ao menos 1 palavra do nome está no título
        if _score(best) == 0:
            return None
        channel_id = best["id"]["channelId"]
        logger.debug("YT channel para '%s' → %s", nome, channel_id)
        return channel_id
    except Exception as exc:
        logger.warning("YouTube discovery falhou para '%s': %s", nome, exc)
        return None


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    project_id = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    logger.info("Candidatos discovery job: project=%s", project_id)

    token = _get_fb_token()
    if not token:
        logger.error("META_APP_TOKEN não configurado — abortando")
        sys.exit(1)

    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery não instalado")
        sys.exit(1)

    bq_client = bigquery.Client(project=project_id)

    df_candidatos = bq_client.query(QUERY_CANDIDATOS.format(project=project_id)).to_dataframe()
    logger.info("fact_candidato_eleicao: %d candidatos encontrados", len(df_candidatos))

    if df_candidatos.empty:
        logger.info("Nenhum candidato no Gold — nada a processar")
        return

    yt_enabled = bool(_YT_API_KEY)
    if not yt_enabled:
        logger.warning("YOUTUBE_API_KEY ausente — descoberta YouTube desabilitada")

    rows: list[dict] = []
    fb_found = 0
    yt_found = 0

    for _, row in df_candidatos.iterrows():
        nome = row["candidato"]

        # Facebook: URL direta → fallback search
        page = find_facebook_page(nome, token)
        if page:
            fb_found += 1

        # YouTube: channel search (apenas se chave disponível)
        yt_channel = discover_youtube_channel(nome) if yt_enabled else None
        if yt_channel:
            yt_found += 1

        rows.append(
            {
                "candidato_id": nome,
                "nome_candidato": nome,
                "facebook_page_id": page["id"] if page else None,
                "facebook_page_name": page.get("name") if page else None,
                "followers_fb": int(page.get("fan_count", 0)) if page else 0,
                "is_verified": (
                    page.get("verification_status") == "blue_verified" if page else False
                ),
                "instagram_handle": None,
                "youtube_channel_id": yt_channel,
                "twitter_handle": None,
                "tiktok_handle": None,
                "updated_at": pd.Timestamp.utcnow(),
            }
        )

    logger.info(
        "Resultado: %d candidatos | %d FB pages | %d YT channels",
        len(rows),
        fb_found,
        yt_found,
    )

    df_rows = pd.DataFrame(rows)
    staging_ref = _STAGING_TABLE.format(project=project_id)
    main_ref = f"{project_id}.spepe_silver.dim_candidato_social_pages"

    # Escreve staging com WRITE_TRUNCATE (substitui execução anterior)
    schema = [bigquery.SchemaField(f["name"], f["type"]) for f in _BQ_SCHEMA]
    stage_cfg = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=schema,
    )
    try:
        bq_client.load_table_from_dataframe(df_rows, staging_ref, job_config=stage_cfg).result()
        logger.info("Staging carregado: %d rows → %s", len(df_rows), staging_ref)
    except Exception as exc:
        logger.error("Falha ao carregar staging: %s", exc)
        sys.exit(1)

    # MERGE: atualiza tabela principal preservando entradas manuais
    try:
        bq_client.query(MERGE_SQL.format(project=project_id)).result()
        logger.info("MERGE concluído em %s", main_ref)
    except Exception as exc:
        # Se tabela não existir ainda, cria a partir do staging
        if "Not found" in str(exc) or "does not exist" in str(exc):
            logger.info("dim_candidato_social_pages não existe — criando a partir do staging")
            create_sql = f"""
                CREATE TABLE `{main_ref}` AS
                SELECT * FROM `{staging_ref}`
            """
            bq_client.query(create_sql).result()
            logger.info("Tabela criada com %d rows", len(df_rows))
        else:
            logger.error("MERGE falhou: %s", exc)
            sys.exit(1)

    logger.info("Candidatos discovery job concluído")


if __name__ == "__main__":
    main()
