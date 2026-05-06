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

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.gdelt")

_GDELT_DOC_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_RECORDS = 250  # API hard limit


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=10, max=60))
def _gdelt_get(params: dict[str, Any]) -> dict:
    resp = requests.get(_GDELT_DOC_BASE, params=params, timeout=60)
    if resp.status_code == 429:
        logger.warning("GDELT rate limit — aguardando 30s")
        time.sleep(30)
        resp = requests.get(_GDELT_DOC_BASE, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


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

    Args:
        candidatos: nomes a buscar
        dias: janela em dias (max 3 meses na API gratuita)
        max_por_candidato: até 250 (limite da API)
        lang: idioma de filtragem GDELT
        domain_filter: sufixo de domínio (default ".br" → imprensa BR)
    """
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
            data = _gdelt_get(params)
        except Exception as exc:
            logger.warning("GDELT falhou para '%s': %s", candidato, exc)
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
                    "resumo": (art.get("socialimage") or "")[:10],  # GDELT artlist não tem resumo
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
        time.sleep(1)  # rate limit polite

    logger.info("GDELT total: %d artigos para %d candidatos", len(results), len(candidatos))
    return results


def fetch_gdelt_timeline_sentiment(
    candidato: str,
    dias: int = 30,
) -> list[dict]:
    """Retorna timeline de volume + sentimento médio por dia para um candidato.

    Útil para plotar evolução do sentimento ao longo do tempo.
    Retorna: [{data, volume, avg_tone, candidato}]
    """
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
        data = _gdelt_get(params)
    except Exception as exc:
        logger.warning("GDELT timeline falhou para '%s': %s", candidato, exc)
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
    """Convert GDELT seendate '20260506T120000Z' → ISO '2026-05-06T12:00:00Z'."""
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
    """Map known Brazilian news domains to source registry keys."""
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
