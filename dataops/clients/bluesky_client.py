"""Bluesky (AT Protocol) client — sem credenciais, API pública.

Bluesky cresceu no Brasil especialmente entre jornalistas, parlamentares
e ativistas políticos após a suspensão do Twitter/X em 2024.

API pública: https://public.api.bsky.app/xrpc/
  - Sem API key nem autenticação para leitura de conteúdo público
  - Rate limit: ~3000 req/5min por IP (muito permissivo)
  - Endpoint: app.bsky.feed.searchPosts
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.bluesky")

_BSKY_BASE = "https://public.api.bsky.app/xrpc"
_SEARCH_ENDPOINT = f"{_BSKY_BASE}/app.bsky.feed.searchPosts"

_POSITIVE_TERMS = {
    "ótimo", "excelente", "apoio", "voto", "melhor", "progresso",
    "esperança", "confiança", "bom", "parabéns", "aprovado", "correto",
    "lidera", "vitória", "conquista",
}
_NEGATIVE_TERMS = {
    "horrível", "péssimo", "corrupto", "mentiroso", "ladrão", "vergonha",
    "contra", "nunca", "repúdio", "pior", "errado", "denúncia",
    "escândalo", "fraude", "rejeição",
}


def _simple_sentiment(text: str) -> str:
    lower = text.lower()
    pos = sum(1 for t in _POSITIVE_TERMS if t in lower)
    neg = sum(1 for t in _NEGATIVE_TERMS if t in lower)
    if pos > neg:
        return "positivo"
    if neg > pos:
        return "negativo"
    return "neutro"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
def _bsky_get(params: dict[str, Any]) -> dict:
    resp = requests.get(_SEARCH_ENDPOINT, params=params, timeout=30)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 30))
        logger.warning("Bluesky rate limit — aguardando %ds", retry_after)
        time.sleep(retry_after)
        resp = requests.get(_SEARCH_ENDPOINT, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_bluesky_mentions(
    candidatos: list[str],
    dias: int = 7,
    max_por_candidato: int = 100,
    lang: str = "pt",
) -> list[dict]:
    """Busca posts públicos no Bluesky que mencionam candidatos brasileiros.

    Retorna lista de dicts com:
      candidato, post_id, text, created_at, like_count, repost_count,
      reply_count, author_handle, sentiment, fonte="bluesky", lang="pt"

    Args:
        candidatos: lista de nomes a buscar
        dias: janela de busca (Bluesky indexa últimas semanas)
        max_por_candidato: máximo de posts por candidato
        lang: filtro de idioma (ISO 639-1)
    """
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    results: list[dict] = []

    for candidato in candidatos:
        collected = 0
        cursor: str | None = None

        while collected < max_por_candidato:
            params: dict[str, Any] = {
                "q": candidato,
                "lang": lang,
                "limit": min(max_por_candidato - collected, 100),
                "since": since,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                data = _bsky_get(params)
            except Exception as exc:
                logger.warning("Bluesky search falhou para '%s': %s", candidato, exc)
                break

            posts = data.get("posts", [])
            if not posts:
                break

            for post in posts:
                record = post.get("record", {})
                text = record.get("text", "")
                author = post.get("author", {})
                counts = post.get("likeCount", 0), post.get("repostCount", 0), post.get("replyCount", 0)

                results.append(
                    {
                        "candidato": candidato,
                        "post_id": post.get("uri", ""),
                        "text": text[:500],
                        "created_at": record.get("createdAt", ""),
                        "like_count": post.get("likeCount", 0),
                        "repost_count": post.get("repostCount", 0),
                        "reply_count": post.get("replyCount", 0),
                        "author_handle": author.get("handle", ""),
                        "sentiment": _simple_sentiment(text),
                        "fonte": "bluesky",
                        "lang": lang,
                    }
                )

            collected += len(posts)
            cursor = data.get("cursor")
            if not cursor:
                break

            time.sleep(0.5)

        logger.info("Bluesky: %d posts para '%s'", collected, candidato)

    logger.info("Bluesky total: %d posts para %d candidatos", len(results), len(candidatos))
    return results
