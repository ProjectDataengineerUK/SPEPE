"""Social media client — Twitter/X API v2 + Facebook Graph API + YouTube Data API v3.

Twitter/X: recent search (free tier — 500k tweets/month, 7-day lookback).
Facebook:  Page posts + reactions via Graph API (requires page token).
YouTube:   Search + video stats via Data API v3 (requires YOUTUBE_API_KEY).

All fetch functions add 'fonte' field compatible with source_registry.SOURCE_REGISTRY.
Call enrich_with_source_meta() after fetch to add score_confiabilidade + tipo_fonte.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from security.secret_manager import get_secret

logger = logging.getLogger("spepe.clients.social")

# ── Twitter/X API v2 ────────────────────────────────────────────────────────
_X_BEARER = os.environ.get("X_BEARER_TOKEN", "")
_X_BASE = "https://api.twitter.com/2"
_X_SEARCH_URL = f"{_X_BASE}/tweets/search/recent"
_X_MAX_RESULTS = 100  # max per request on free tier

# ── Facebook Graph API ──────────────────────────────────────────────────────
_FB_BASE = "https://graph.facebook.com/v19.0"

# ── YouTube Data API v3 ─────────────────────────────────────────────────────
# Note: _YT_API_KEY is resolved at call time via _get_yt_key() so that Secret Manager
# values set after module import are picked up correctly.
_YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_YT_MAX_RESULTS = 50  # max per request


def _get_yt_key() -> str:
    """Resolve YouTube API key: Secret Manager first, then env var."""
    return get_secret("YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_API_KEY", "")


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


def _get_fb_token() -> str:
    # Prefer Secret Manager so prod never falls back to env alone
    return get_secret("META_APP_TOKEN") or os.environ.get("META_APP_TOKEN", "")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fb_get(path: str, params: dict[str, Any]) -> dict:
    token = _get_fb_token()
    if not token:
        raise RuntimeError("META_APP_TOKEN não configurado")
    params = {"access_token": token, **params}
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
    token = _get_fb_token()
    if not token:
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


def fetch_instagram_posts(
    instagram_handle: str,
    days_back: int = 30,
) -> pd.DataFrame:
    token = _get_fb_token()
    if not token:
        logger.warning("META_APP_TOKEN ausente — retornando DataFrame vazio para Instagram")
        return pd.DataFrame(
            columns=[
                "text",
                "like_count",
                "comment_count",
                "created_at",
                "fonte",
                "platform_post_id",
            ]
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        # Instagram Business accounts are addressed by handle via the Graph API
        resp = requests.get(
            f"{_FB_BASE}/{instagram_handle}/media",
            params={
                "fields": "id,caption,like_count,comments_count,timestamp",
                "access_token": token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Instagram fetch falhou para '%s': %s", instagram_handle, exc)
        return pd.DataFrame(
            columns=[
                "text",
                "like_count",
                "comment_count",
                "created_at",
                "fonte",
                "platform_post_id",
            ]
        )

    rows: list[dict] = []
    for item in data.get("data", []):
        ts_str = item.get("timestamp", "")
        try:
            created_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        if created_at < cutoff:
            continue

        rows.append(
            {
                # caption is optional — Instagram allows image-only posts
                "text": item.get("caption", "") or "",
                "like_count": item.get("like_count", 0),
                "comment_count": item.get("comments_count", 0),
                "created_at": created_at,
                "fonte": "instagram",
                "platform_post_id": item.get("id", ""),
            }
        )

    logger.info("Instagram: %d posts coletados para handle '%s'", len(rows), instagram_handle)
    return pd.DataFrame(rows)


# ── YouTube Data API v3 ───────────────────────────────────────────────────────


def fetch_youtube_videos(
    candidatos: list[str],
    year: int,
    max_por_candidato: int = 200,
) -> list[dict]:
    """Busca vídeos e estatísticas do YouTube relacionados a candidatos.

    Retorna lista de dicts com: candidato, video_id, title, published_at,
    view_count, like_count, comment_count, sentiment, channel_title, fonte.

    Requer YOUTUBE_API_KEY (Google Cloud Console, YouTube Data API v3 habilitada).
    Quota: 10.000 unidades/dia grátis; search.list = 100 unidades/chamada.
    """
    yt_key = _get_yt_key()
    if not yt_key:
        logger.warning("YOUTUBE_API_KEY ausente — retornando lista vazia")
        return []

    published_after = f"{year}-01-01T00:00:00Z"
    published_before = f"{year}-12-31T23:59:59Z"
    results: list[dict] = []

    for candidato in candidatos:
        video_ids: list[str] = []
        page_token: str | None = None
        collected = 0

        while collected < max_por_candidato:
            params: dict[str, Any] = {
                "key": yt_key,
                "q": candidato,
                "part": "snippet",
                "type": "video",
                "maxResults": min(max_por_candidato - collected, _YT_MAX_RESULTS),
                "publishedAfter": published_after,
                "publishedBefore": published_before,
                "relevanceLanguage": "pt",
                "regionCode": "BR",
                "order": "relevance",
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                resp = requests.get(_YT_SEARCH_URL, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("YouTube search falhou para '%s': %s", candidato, exc)
                break

            items = data.get("items", [])
            if not items:
                break

            for item in items:
                vid_id = item.get("id", {}).get("videoId")
                if vid_id:
                    video_ids.append(vid_id)
            collected += len(items)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if not video_ids:
            continue

        # Busca estatísticas em lote (50 IDs por request)
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            try:
                stats_resp = requests.get(
                    _YT_VIDEOS_URL,
                    params={
                        "key": yt_key,
                        "id": ",".join(batch),
                        "part": "snippet,statistics",
                    },
                    timeout=30,
                )
                stats_resp.raise_for_status()
                stats_data = stats_resp.json()
            except Exception as exc:
                logger.warning("YouTube statistics falhou: %s", exc)
                continue

            for v in stats_data.get("items", []):
                snippet = v.get("snippet", {})
                stats = v.get("statistics", {})
                title = snippet.get("title", "")
                results.append(
                    {
                        "candidato": candidato,
                        "video_id": v["id"],
                        "title": title[:200],
                        "channel_title": snippet.get("channelTitle", ""),
                        "published_at": snippet.get("publishedAt", ""),
                        "view_count": int(stats.get("viewCount", 0) or 0),
                        "like_count": int(stats.get("likeCount", 0) or 0),
                        "comment_count": int(stats.get("commentCount", 0) or 0),
                        "sentiment": _simple_sentiment(title),
                        "fonte": "youtube",
                    }
                )

    logger.info("YouTube: %d vídeos coletados para %d candidatos", len(results), len(candidatos))
    return results


# ── Vertex AI NLP ────────────────────────────────────────────────────────────


def _call_vertex_nlp(
    texts: list[str],
    project: str,
    location: str = "us-central1",
) -> tuple[list[str], list[float], list[float | None]]:
    """Classify sentiment via Vertex AI text-multilingual-v1 in batches of 25.

    Returns (labels, scores, confidences) aligned with input texts.
    - labels: positivo | negativo | neutro
    - scores: float -1.0 to +1.0 (document_sentiment.score)
    - confidences: float 0.0-1.0 from abs(magnitude) capped at 1.0, or None on fallback
    Falls back to _simple_sentiment if Vertex AI unavailable.
    """
    try:
        from google.cloud import language_v2

        client = language_v2.LanguageServiceClient()
        labels: list[str] = []
        scores: list[float] = []
        confidences: list[float | None] = []

        for text in texts:
            doc = language_v2.Document(
                content=text[:1000],
                type_=language_v2.Document.Type.PLAIN_TEXT,
                language_code="pt",
            )
            try:
                result = client.analyze_sentiment(request={"document": doc})
                score = result.document_sentiment.score
                magnitude = result.document_sentiment.magnitude

                if score >= 0.15:
                    labels.append("positivo")
                elif score <= -0.15:
                    labels.append("negativo")
                else:
                    labels.append("neutro")

                scores.append(float(score))
                # normalize magnitude 0-3 → 0-1 per spec (magnitude rarely exceeds 3)
                confidences.append(min(abs(float(magnitude)) / 3.0, 1.0))

            except Exception:
                labels.append(_simple_sentiment(text))
                scores.append(0.0)
                confidences.append(None)

        return labels, scores, confidences

    except ImportError:
        logger.debug("google-cloud-language indisponível — usando sentiment rule-based")
        fallback_labels = [_simple_sentiment(t) for t in texts]
        fallback_scores = [0.0] * len(texts)
        fallback_confidences: list[float | None] = [None] * len(texts)
        return fallback_labels, fallback_scores, fallback_confidences


def enrich_sentiment_vertex(
    df: pd.DataFrame | list,
    text_field: str = "text",
    project: str = "",
) -> pd.DataFrame | list:
    """Enrich records with Vertex AI NLP sentiment columns.

    Accepts either a DataFrame or a list of dicts; returns the same type.
    Adds/replaces:
      - sentiment       (str)   positivo | negativo | neutro
      - sentimento_score (float) -1.0 to +1.0
      - confianca_nlp   (float) 0.0-1.0, NULL when GCP unavailable

    Falls back to rule-based sentiment when GCP_PROJECT_ID is unset.
    """
    if not project:
        project = os.environ.get("GCP_PROJECT_ID", "")

    was_list = isinstance(df, list)
    if was_list:
        df = pd.DataFrame(df)

    texts = df[text_field].fillna("").tolist() if text_field in df.columns else [""] * len(df)

    if not project:
        df = df.copy()
        df["sentiment"] = [_simple_sentiment(t) for t in texts]
        df["sentimento_score"] = 0.0
        df["confianca_nlp"] = None
        return df.to_dict("records") if was_list else df

    labels, scores, confidences = _call_vertex_nlp(texts, project)

    df = df.copy()
    df["sentiment"] = labels
    df["sentimento_score"] = scores
    df["confianca_nlp"] = confidences

    return df.to_dict("records") if was_list else df


# ── BigQuery helpers ─────────────────────────────────────────────────────────


def load_candidate_pages_from_bq(
    project_id: str,
    bq_table: str = "spepe_silver.dim_candidato_social_pages",
) -> dict[str, list[str]]:
    empty: dict[str, list[str]] = {"facebook": [], "instagram": [], "youtube": []}

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        query = (
            f"SELECT facebook_page_id, instagram_handle, youtube_channel_id "
            f"FROM `{project_id}.{bq_table}` "
            f"WHERE facebook_page_id IS NOT NULL OR instagram_handle IS NOT NULL OR youtube_channel_id IS NOT NULL"
        )
        rows = client.query(query).result()

        result: dict[str, list[str]] = {"facebook": [], "instagram": [], "youtube": []}
        for row in rows:
            if row.facebook_page_id:
                result["facebook"].append(row.facebook_page_id)
            if row.instagram_handle:
                result["instagram"].append(row.instagram_handle)
            if row.youtube_channel_id:
                result["youtube"].append(row.youtube_channel_id)

        return result

    except Exception as exc:
        logger.warning("load_candidate_pages_from_bq falhou — retornando listas vazias: %s", exc)
        return empty


# ── Credibility and coordination heuristics ───────────────────────────────────


def compute_score_credibilidade(df: pd.DataFrame) -> pd.Series:
    base = (
        df["score_confiabilidade"]
        if "score_confiabilidade" in df.columns
        else pd.Series(1.0, index=df.index)
    )
    suspeito = (
        df["suspeito_coordenado"]
        if "suspeito_coordenado" in df.columns
        else pd.Series(False, index=df.index)
    )
    # coordinated posts receive a 70% penalty on source trust score
    penalty = suspeito.map({True: 0.3, False: 1.0}).fillna(1.0)
    return (base * penalty).astype(float)


def detect_suspeito_coordenado(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean column `suspeito_coordenado` to a social DataFrame.

    A post is flagged when the exact same lowercased+stripped text appears
    >= 5 times within a 1-hour window for the same candidato.
    This catches copy-paste brigading campaigns that inflate apparent organic reach.
    """
    df = df.copy()
    df["suspeito_coordenado"] = False

    if df.empty:
        return df

    required = {"text", "created_at", "candidato"}
    if not required.issubset(df.columns):
        logger.warning(
            "detect_suspeito_coordenado: colunas ausentes %s", required - set(df.columns)
        )
        return df

    df["_text_norm"] = df["text"].str.lower().str.strip()
    df["_created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")

    # Round to 1-hour bins per candidato+text to count occurrences
    df["_hour_bin"] = df["_created_at"].dt.floor("h")

    counts = df.groupby(["candidato", "_text_norm", "_hour_bin"])["_text_norm"].transform("count")
    # Posts with text that appears >= 5 times in the same hour+candidato window
    df["suspeito_coordenado"] = counts >= 5

    df.drop(columns=["_text_norm", "_created_at", "_hour_bin"], inplace=True)
    return df
