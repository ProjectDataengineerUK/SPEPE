"""Reddit client — r/brasil e r/politica via Reddit API (OAuth2 app-only).

Requer: REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET no Secret Manager.
Sem pushshift (descontinuado em 2023) — usa Reddit API oficial /r/{sub}/search.json.
Rate limit: 60 requests/min (app-only). Paginação via after= token.
"""

from __future__ import annotations

import logging
import os
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.reddit")

_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
_USER_AGENT = "SPEPE-DataOps/1.0 (analysis platform)"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_SEARCH_URL = "https://oauth.reddit.com/r/{subreddit}/search.json"
_NEW_URL = "https://oauth.reddit.com/r/{subreddit}/new.json"

_SUBREDDITS = ["brasil", "politica", "brasilivre", "bolsonaro"]
_DEFAULT_LIMIT = 100  # max per request

_token_cache: dict = {"token": "", "expires_at": 0.0}


def _get_token() -> str:
    """OAuth2 app-only token with 1h cache."""
    if not _CLIENT_ID or not _CLIENT_SECRET:
        raise RuntimeError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET não configurados")

    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    resp = requests.post(
        _TOKEN_URL,
        auth=(_CLIENT_ID, _CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": _USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return _token_cache["token"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _reddit_get(url: str, params: dict) -> dict:
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 429:
        wait = int(resp.headers.get("Retry-After", 60))
        logger.warning("Reddit rate limit — aguardando %ds", wait)
        time.sleep(wait)
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_post(child: dict, subreddit: str, candidato: str | None = None) -> dict:
    data = child.get("data", {})
    text = f"{data.get('title', '')} {data.get('selftext', '')}".strip()
    return {
        "post_id": data.get("name", ""),
        "subreddit": subreddit,
        "candidato": candidato,
        "title": data.get("title", "")[:300],
        "text": text[:1000],
        "url": data.get("url", ""),
        "author": data.get("author", "[deleted]"),
        "created_utc": data.get("created_utc"),
        "score": data.get("score", 0),
        "upvote_ratio": data.get("upvote_ratio", 0.0),
        "num_comments": data.get("num_comments", 0),
        "is_self": data.get("is_self", False),
        "fonte": "reddit",
    }


def fetch_reddit_mentions(
    candidatos: list[str],
    subreddits: list[str] | None = None,
    max_por_candidato: int = 300,
    dias: int = 30,
) -> list[dict]:
    """Busca menções a candidatos nos subreddits políticos brasileiros.

    Retorna lista de posts com: post_id, subreddit, candidato, title, text,
    score, upvote_ratio, num_comments, created_utc, fonte.
    """
    if not _CLIENT_ID or not _CLIENT_SECRET:
        logger.warning("REDDIT_CLIENT_ID/SECRET ausentes — retornando lista vazia")
        return []

    subs = subreddits or _SUBREDDITS
    results: list[dict] = []

    for candidato in candidatos:
        collected = 0
        after: str | None = None

        for sub in subs:
            while collected < max_por_candidato:
                params: dict = {
                    "q": candidato,
                    "sort": "new",
                    "t": "month" if dias >= 30 else "week",
                    "limit": min(max_por_candidato - collected, _DEFAULT_LIMIT),
                    "restrict_sr": True,
                }
                if after:
                    params["after"] = after

                try:
                    data = _reddit_get(_SEARCH_URL.format(subreddit=sub), params)
                except Exception as exc:
                    logger.warning("Reddit search falhou %s/%s: %s", sub, candidato, exc)
                    break

                children = data.get("data", {}).get("children", [])
                if not children:
                    break

                for child in children:
                    results.append(_parse_post(child, sub, candidato))
                collected += len(children)

                after = data.get("data", {}).get("after")
                if not after:
                    break

                time.sleep(1.0)  # 60 req/min = ~1s entre chamadas

        logger.info("Reddit: %d posts para '%s'", collected, candidato)

    return results


def fetch_reddit_subreddit_new(
    subreddit: str,
    limit: int = 500,
) -> list[dict]:
    """Busca posts recentes de um subreddit (sem filtro de candidato).

    Útil para monitoramento de narrativas emergentes.
    """
    if not _CLIENT_ID or not _CLIENT_SECRET:
        return []

    results: list[dict] = []
    after: str | None = None

    while len(results) < limit:
        params: dict = {"limit": min(limit - len(results), _DEFAULT_LIMIT)}
        if after:
            params["after"] = after

        try:
            data = _reddit_get(_NEW_URL.format(subreddit=subreddit), params)
        except Exception as exc:
            logger.warning("Reddit /new falhou %s: %s", subreddit, exc)
            break

        children = data.get("data", {}).get("children", [])
        if not children:
            break

        for child in children:
            results.append(_parse_post(child, subreddit))

        after = data.get("data", {}).get("after")
        if not after:
            break
        time.sleep(1.0)

    logger.info("Reddit /new %s: %d posts", subreddit, len(results))
    return results
