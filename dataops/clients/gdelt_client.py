"""GDELT DOC 2.0 API client — cobertura de imprensa brasileira.

GDELT (Global Database of Events, Language, and Tone) monitora toda mídia
impressa e digital do mundo em tempo real. Sem API key — totalmente grátis.

API DOC 2.0: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
  - Atualização: a cada 15 minutos
  - Cobertura: notícias em português do Brasil
  - Sentimento: AvgTone pré-calculado (negativo < 0 < positivo)
  - Modos: artlist (lista de artigos), timelinevol, timelinesenti
"""

from __future__ import annotations

import io
import logging
import os
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.gdelt")

_GDELT_DOC_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_RECORDS = 250
_CACHE_MAX_AGE_SECONDS = 30 * 60  # 30 minutes


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type((requests.HTTPError, requests.ConnectionError)),
    reraise=False,
)
def _fetch_with_retry(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _gcs_cache_path(date_str: str) -> str:
    bucket = os.environ.get("GCS_BUCKET", "")
    return f"gs://{bucket}/cache/gdelt/{date_str}.parquet"


def _read_gcs_cache(date_str: str) -> pd.DataFrame | None:
    bucket = os.environ.get("GCS_BUCKET", "")
    if not bucket:
        return None
    try:
        from google.cloud import storage

        gcs = storage.Client()
        blob_path = f"cache/gdelt/{date_str}.parquet"
        bucket_obj = gcs.bucket(bucket)
        blob = bucket_obj.blob(blob_path)
        if not blob.exists():
            return None
        blob.reload()
        age = (datetime.now(timezone.utc) - blob.updated).total_seconds()
        if age > _CACHE_MAX_AGE_SECONDS:
            return None
        data = blob.download_as_bytes()
        df = pd.read_parquet(io.BytesIO(data))
        logger.info("GDELT cache hit: %s (%d rows, age=%.0fs)", blob_path, len(df), age)
        return df
    except Exception as exc:
        logger.warning("GDELT cache read failed — proceeding without cache: %s", exc)
        return None


def _write_gcs_cache(date_str: str, df: pd.DataFrame) -> None:
    bucket = os.environ.get("GCS_BUCKET", "")
    if not bucket:
        return
    try:
        from google.cloud import storage

        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        gcs = storage.Client()
        blob_path = f"cache/gdelt/{date_str}.parquet"
        gcs.bucket(bucket).blob(blob_path).upload_from_file(buf, content_type="application/octet-stream")
        logger.info("GDELT cache written: %s (%d rows)", blob_path, len(df))
    except Exception as exc:
        logger.warning("GDELT cache write failed — continuing: %s", exc)


def fetch_gdelt_events(
    candidatos: list[str],
    dias: int = 7,
    max_por_candidato: int = 250,
    lang: str = "Portuguese",
    domain_filter: str = ".br",
) -> pd.DataFrame:
    """Alias returning DataFrame — used by social_ingest_job."""
    if os.environ.get("GDELT_ENABLED", "true").lower() != "true":
        logger.info("GDELT desabilitado via GDELT_ENABLED=false — retornando DataFrame vazio")
        return pd.DataFrame()

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    cached = _read_gcs_cache(date_str)
    if cached is not None:
        return cached

    records = fetch_gdelt_mentions(
        candidatos=candidatos,
        dias=dias,
        max_por_candidato=max_por_candidato,
        lang=lang,
        domain_filter=domain_filter,
    )
    df = pd.DataFrame(records) if records else pd.DataFrame()
    if not df.empty:
        _write_gcs_cache(date_str, df)
    return df


def fetch_gdelt_mentions(
    candidatos: list[str],
    dias: int = 7,
    max_por_candidato: int = 250,
    lang: str = "Portuguese",
    domain_filter: str = ".br",
) -> list[dict]:
    """Busca artigos de imprensa BR no GDELT que mencionam cada candidato.

    Retorna lista de dicts com:
      candidato, titulo, url, dominio, data_publicacao, resumo,
      sentiment (positivo|negativo|neutro), avg_tone, fonte="gdelt",
      pais="BR", lang="pt"
    """
    if os.environ.get("GDELT_ENABLED", "true").lower() != "true":
        logger.info("GDELT desabilitado via GDELT_ENABLED=false — retornando lista vazia")
        return []

    since_dt = datetime.now(timezone.utc) - timedelta(days=dias)
    since_str = since_dt.strftime("%Y%m%d%H%M%S")

    results: list[dict] = []

    for candidato in candidatos:
        query_parts = [
            f'"{candidato}"',
            f"sourcelang:{lang}",
            f"domainis:{domain_filter}",
        ]
        query = " ".join(query_parts)

        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": min(max_por_candidato, _MAX_RECORDS),
            "startdatetime": since_str,
            "format": "json",
            "sort": "DateDesc",
        }

        try:
            data = _fetch_with_retry(_GDELT_DOC_BASE, params)
        except Exception as exc:
            logger.warning("GDELT falhou para '%s': %s", candidato, exc)
            continue

        if data is None:
            logger.warning("GDELT retornou None para '%s' — pulando", candidato)
            continue

        articles = data.get("articles", [])
        if not articles:
            logger.info("GDELT: nenhum artigo para '%s' nos últimos %d dias", candidato, dias)
            continue

        for art in articles:
            tone = float(art.get("tone", 0) or 0)
            sentiment = _tone_to_sentiment(tone)
            dominio = _extract_domain(art.get("url", ""))
            fonte_key = _domain_to_fonte_key(dominio)

            results.append(
                {
                    "candidato": candidato,
                    "titulo": (art.get("title") or "")[:300],
                    "url": art.get("url", ""),
                    "dominio": dominio,
                    "data_publicacao": _parse_gdelt_date(art.get("seendate", "")),
                    "resumo": (art.get("socialimage") or "")[:10],
                    "avg_tone": round(tone, 3),
                    "sentiment": sentiment,
                    "fonte": "gdelt",
                    "fonte_especifica": fonte_key,
                    "pais": "BR",
                    "lang": "pt",
                    "created_at": _parse_gdelt_date(art.get("seendate", "")),
                }
            )

        logger.info("GDELT: %d artigos para '%s'", len(articles), candidato)
        time.sleep(1)

    logger.info("GDELT total: %d artigos para %d candidatos", len(results), len(candidatos))
    return results


def fetch_gdelt_timeline_sentiment(
    candidato: str,
    dias: int = 30,
) -> list[dict]:
    """Retorna timeline de volume + sentimento médio por dia para um candidato."""
    since_dt = datetime.now(timezone.utc) - timedelta(days=dias)
    since_str = since_dt.strftime("%Y%m%d%H%M%S")

    params = {
        "query": f'"{candidato}" sourcelang:Portuguese domainis:.br',
        "mode": "timelinesenti",
        "startdatetime": since_str,
        "format": "json",
        "smoothing": "3",
    }

    try:
        data = _fetch_with_retry(_GDELT_DOC_BASE, params)
    except Exception as exc:
        logger.warning("GDELT timeline falhou para '%s': %s", candidato, exc)
        return []

    if data is None:
        return []

    results = []
    for series in data.get("timeline", []):
        for point in series.get("data", []):
            results.append(
                {
                    "candidato": candidato,
                    "data": point.get("date", ""),
                    "volume": point.get("value", 0),
                    "avg_tone": point.get("tone", 0),
                    "fonte": "gdelt",
                }
            )
    return results


# ── helpers ──────────────────────────────────────────────────────────────────


def _tone_to_sentiment(tone: float) -> str:
    if tone >= 1.0:
        return "positivo"
    if tone <= -1.0:
        return "negativo"
    return "neutro"


def _parse_gdelt_date(seendate: str) -> str:
    if not seendate:
        return ""
    try:
        clean = seendate.replace("T", "").replace("Z", "")
        dt = datetime.strptime(clean, "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return seendate


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _domain_to_fonte_key(domain: str) -> str:
    mapping = {
        "g1.globo.com": "g1_globo",
        "globo.com": "g1_globo",
        "oglobo.globo.com": "o_globo",
        "folha.uol.com.br": "folha",
        "estadao.com.br": "estadao",
        "poder360.com.br": "poder360",
        "agenciabrasil.ebc.com.br": "agencia_brasil",
        "cnnbrasil.com.br": "cnn_brasil",
        "uol.com.br": "uol",
        "r7.com": "r7",
        "veja.abril.com.br": "veja",
        "exame.com": "exame",
        "metropoles.com": "metropoles",
        "agenciapublica.org.br": "agencia_publica",
    }
    for k, v in mapping.items():
        if k in domain:
            return v
    return "gdelt"
