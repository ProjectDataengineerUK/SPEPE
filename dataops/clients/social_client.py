"""Social media client — Twitter/X API v2 + Facebook Graph API.

Twitter/X: recent search (free tier — 500k tweets/month, 7-day lookback).
Facebook:  Page posts + reactions via Graph API (requires page token).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.social")

# ── Twitter/X API v2 ────────────────────────────────────────────────────────
_X_BEARER = os.environ.get("X_BEARER_TOKEN", "")
_X_BASE = "https://api.twitter.com/2"
_X_SEARCH_URL = f"{_X_BASE}/tweets/search/recent"
_X_MAX_RESULTS = 100  # max per request on free tier

# ── Facebook Graph API ──────────────────────────────────────────────────────
_FB_TOKEN = os.environ.get("META_APP_TOKEN", "")
_FB_BASE = "https://graph.facebook.com/v19.0"


# ── Sentiment labels (rule-based — substituir por Vertex AI NLP na Fase 2+) ─
_POSITIVE_TERMS = {
    "ótimo",
    "excelente",
    "apoio",
    "voto",
    "melhor",
    "progresso",
    "esperança",
    "confiança",
    "bom",
    "parabéns",
    "aprovado",
    "correto",
}
_NEGATIVE_TERMS = {
    "horrível",
    "péssimo",
    "corrupto",
    "mentiroso",
    "ladrão",
    "vergonha",
    "contra",
    "nunca",
    "repudio",
    "pior",
    "errado",
    "denúncia",
}


def _simple_sentiment(text: str) -> str:
    """Rule-based sentiment: positive | negative | neutro."""
    lower = text.lower()
    pos = sum(1 for t in _POSITIVE_TERMS if t in lower)
    neg = sum(1 for t in _NEGATIVE_TERMS if t in lower)
    if pos > neg:
        return "positivo"
    if neg > pos:
        return "negativo"
    return "neutro"


# ── Twitter/X ───────────────────────────────────────────────────────────────


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
def _x_get(url: str, params: dict[str, Any]) -> dict:
    if not _X_BEARER:
        raise RuntimeError("X_BEARER_TOKEN não configurado")
    headers = {"Authorization": f"Bearer {_X_BEARER}"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 429:
        reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
        wait = max(reset - int(time.time()), 5)
        logger.warning("X rate limit — aguardando %ds", wait)
        time.sleep(wait)
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_x_mentions(
    candidatos: list[str],
    dias: int = 7,
    max_por_candidato: int = 500,
) -> list[dict]:
    """Busca menções recentes no Twitter/X para lista de candidatos.

    Retorna lista de dicts com: candidato, tweet_id, text, created_at,
    like_count, retweet_count, reply_count, sentiment, lang.
    Free tier: últimos 7 dias, até 500k tweets/mês total.
    """
    if not _X_BEARER:
        logger.warning("X_BEARER_TOKEN ausente — retornando lista vazia")
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for candidato in candidatos:
        query = f'"{candidato}" lang:pt -is:retweet'
        params: dict[str, Any] = {
            "query": query,
            "max_results": min(max_por_candidato, _X_MAX_RESULTS),
            "start_time": since,
            "tweet.fields": "created_at,public_metrics,lang",
            "expansions": "author_id",
        }
        collected = 0
        next_token: str | None = None

        while collected < max_por_candidato:
            if next_token:
                params["next_token"] = next_token

            try:
                data = _x_get(_X_SEARCH_URL, params)
            except Exception as exc:
                logger.warning("X search falhou para '%s': %s", candidato, exc)
                break

            tweets = data.get("data", [])
            if not tweets:
                break

            for tw in tweets:
                metrics = tw.get("public_metrics", {})
                results.append(
                    {
                        "candidato": candidato,
                        "tweet_id": tw["id"],
                        "text": tw["text"],
                        "created_at": tw.get("created_at", ""),
                        "like_count": metrics.get("like_count", 0),
                        "retweet_count": metrics.get("retweet_count", 0),
                        "reply_count": metrics.get("reply_count", 0),
                        "lang": tw.get("lang", "pt"),
                        "sentiment": _simple_sentiment(tw["text"]),
                        "fonte": "twitter_x",
                    }
                )
            collected += len(tweets)

            meta = data.get("meta", {})
            next_token = meta.get("next_token")
            if not next_token or collected >= max_por_candidato:
                break

        logger.info("Twitter/X: %d tweets coletados para '%s'", collected, candidato)

    return results


def aggregate_x_sentiment(
    mentions: list[dict],
    by: str = "candidato",
) -> list[dict]:
    """Agrega sentimento por candidato (ou outro campo 'by').

    Retorna: [{candidato, total, positivo, negativo, neutro, score_liquido}]
    score_liquido = (positivo - negativo) / total * 100
    """
    from collections import defaultdict

    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "positivo": 0, "negativo": 0, "neutro": 0}
    )
    for m in mentions:
        key = m.get(by, "desconhecido")
        buckets[key]["total"] += 1
        buckets[key][m.get("sentiment", "neutro")] += 1

    result = []
    for key, counts in buckets.items():
        total = counts["total"] or 1
        result.append(
            {
                by: key,
                "total_mencoes": counts["total"],
                "pct_positivo": round(counts["positivo"] / total * 100, 1),
                "pct_negativo": round(counts["negativo"] / total * 100, 1),
                "pct_neutro": round(counts["neutro"] / total * 100, 1),
                "score_liquido": round((counts["positivo"] - counts["negativo"]) / total * 100, 1),
            }
        )
    return sorted(result, key=lambda x: x["total_mencoes"], reverse=True)


# ── Facebook Graph API ───────────────────────────────────────────────────────


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fb_get(path: str, params: dict[str, Any]) -> dict:
    if not _FB_TOKEN:
        raise RuntimeError("META_APP_TOKEN não configurado")
    params = {"access_token": _FB_TOKEN, **params}
    resp = requests.get(f"{_FB_BASE}/{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_fb_page_posts(
    page_ids: list[str],
    dias: int = 30,
) -> list[dict]:
    """Busca posts recentes de páginas políticas no Facebook.

    Retorna lista de dicts com: page_id, post_id, message, created_time,
    likes, comments, shares, sentiment.
    """
    if not _FB_TOKEN:
        logger.warning("META_APP_TOKEN ausente — retornando lista vazia")
        return []

    since = int((datetime.now(timezone.utc) - timedelta(days=dias)).timestamp())
    results = []

    for page_id in page_ids:
        try:
            data = _fb_get(
                f"{page_id}/posts",
                {
                    "fields": "message,created_time,likes.summary(true),comments.summary(true),shares",
                    "since": since,
                    "limit": 50,
                },
            )
        except Exception as exc:
            logger.warning("Facebook posts falhou para page %s: %s", page_id, exc)
            continue

        for post in data.get("data", []):
            msg = post.get("message", "")
            results.append(
                {
                    "page_id": page_id,
                    "post_id": post.get("id", ""),
                    "message": msg[:500],
                    "created_time": post.get("created_time", ""),
                    "likes": post.get("likes", {}).get("summary", {}).get("total_count", 0),
                    "comments": post.get("comments", {}).get("summary", {}).get("total_count", 0),
                    "shares": post.get("shares", {}).get("count", 0) if "shares" in post else 0,
                    "sentiment": _simple_sentiment(msg),
                    "fonte": "facebook",
                }
            )

    logger.info("Facebook: %d posts coletados de %d páginas", len(results), len(page_ids))
    return results
