"""RSS news client — portais de notícias brasileiros.

Lê feeds RSS públicos de G1, Folha, Poder360, Agência Brasil, CNN Brasil,
R7 e outros. Sem API key — totalmente grátis.

Detecta menções de candidatos por nome (case-insensitive substring match)
e aplica análise de sentimento rule-based no título + descrição.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

logger = logging.getLogger("spepe.clients.news_rss")

# ── Feed registry — mapeado para chaves do source_registry ───────────────────
FEED_REGISTRY: dict[str, dict[str, str]] = {
    "g1_globo": {
        "url": "https://g1.globo.com/rss/g1/politica/",
        "nome": "G1 Política",
    },
    "o_globo": {
        "url": "https://oglobo.globo.com/rss.xml",
        "nome": "O Globo",
    },
    "poder360": {
        "url": "https://www.poder360.com.br/feed/",
        "nome": "Poder360",
    },
    "agencia_brasil": {
        "url": "https://agenciabrasil.ebc.com.br/rss/politica/feed.xml",
        "nome": "Agência Brasil",
    },
    "cnn_brasil": {
        "url": "https://www.cnnbrasil.com.br/politica/feed/",
        "nome": "CNN Brasil",
    },
    "uol": {
        "url": "https://rss.uol.com.br/feed/politica.xml",
        "nome": "UOL Política",
    },
    "r7": {
        "url": "https://noticias.r7.com/brasil/feed.xml",
        "nome": "R7 Brasil",
    },
    "metropoles": {
        "url": "https://www.metropoles.com/brasil/politica-brasil/feed",
        "nome": "Metrópoles Política",
    },
    "agencia_publica": {
        "url": "https://apublica.org/feed/",
        "nome": "Agência Pública",
    },
}

# ── Sentiment — shared lexicon ────────────────────────────────────────────────
_POSITIVE_TERMS = {
    "aprovação",
    "cresce",
    "vence",
    "lidera",
    "vitória",
    "positivo",
    "apoio",
    "conquista",
    "melhora",
    "progresso",
    "esperança",
    "confiança",
    "aprovado",
    "recorde",
    "alta",
}
_NEGATIVE_TERMS = {
    "corrupção",
    "escândalo",
    "crise",
    "queda",
    "preso",
    "condenado",
    "denúncia",
    "fraude",
    "impeachment",
    "reprovação",
    "rejeição",
    "protesto",
    "colapso",
    "falso",
    "mentira",
    "derrota",
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


def _parse_rss_date(date_str: str) -> str:
    """Parse RFC 2822 date (RSS pubDate) to ISO 8601."""
    if not date_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return date_str


def _fetch_feed_raw(url: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Fetch RSS feed using requests + manual XML parse (no external library needed).

    Uses feedparser if available, falls back to basic XML parse.
    Returns list of raw items [{title, link, description, pubDate, ...}].
    """
    try:
        import feedparser  # type: ignore

        parsed = feedparser.parse(url)
        items = []
        for entry in parsed.entries:
            items.append(
                {
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "description": getattr(entry, "summary", ""),
                    "pubDate": getattr(entry, "published", ""),
                }
            )
        return items
    except ImportError:
        pass

    # Fallback: manual RSS parse via requests + xml.etree
    import xml.etree.ElementTree as ET

    try:
        headers = {"User-Agent": "SPEPE-NewsBot/1.0 (electoral-research@spepe.br)"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []

        # RSS 2.0 items
        for item in root.findall(".//item"):
            items.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "description": (item.findtext("description") or "").strip(),
                    "pubDate": (item.findtext("pubDate") or "").strip(),
                }
            )

        # Atom entries (fallback)
        if not items:
            for entry in root.findall(".//atom:entry", ns):
                link_el = entry.find("atom:link", ns)
                items.append(
                    {
                        "title": (entry.findtext("atom:title", namespaces=ns) or "").strip(),
                        "link": link_el.get("href", "") if link_el is not None else "",
                        "description": (
                            entry.findtext("atom:summary", namespaces=ns) or ""
                        ).strip(),
                        "pubDate": (entry.findtext("atom:published", namespaces=ns) or "").strip(),
                    }
                )
        return items
    except Exception as exc:
        logger.warning("RSS parse failed for %s: %s", url, exc)
        return []


def _mentions_candidato(text: str, candidato: str) -> bool:
    """Case-insensitive substring check for candidate name (or any token)."""
    lower = text.lower()
    tokens = candidato.lower().split()
    # Match if ALL tokens appear (handles "Lula da Silva" → "lula" AND "silva")
    return all(tok in lower for tok in tokens)


def fetch_rss_mentions(
    candidatos: list[str],
    feeds: dict[str, dict[str, str]] | None = None,
    min_text_len: int = 20,
) -> list[dict]:
    """Fetch all configured RSS feeds and filter articles mentioning candidatos.

    Returns list of dicts with:
      candidato, titulo, url, dominio, data_publicacao, resumo,
      sentiment, fonte, fonte_key, pais, lang, created_at
    """
    if feeds is None:
        feeds = FEED_REGISTRY

    results: list[dict] = []

    for fonte_key, feed_info in feeds.items():
        url = feed_info["url"]
        logger.info("RSS: coletando '%s' (%s)", feed_info["nome"], url)

        items = _fetch_feed_raw(url)
        if not items:
            logger.warning("RSS: feed vazio ou inacessível — %s", url)
            continue

        dominio = _extract_domain(url)
        matched = 0

        for item in items:
            titulo = item.get("title", "")
            descricao = item.get("description", "") or ""
            full_text = f"{titulo} {descricao}"

            if len(full_text.strip()) < min_text_len:
                continue

            for candidato in candidatos:
                if _mentions_candidato(full_text, candidato):
                    sentiment = _simple_sentiment(full_text)
                    results.append(
                        {
                            "candidato": candidato,
                            "titulo": titulo[:300],
                            "url": item.get("link", "")[:500],
                            "dominio": dominio,
                            "data_publicacao": _parse_rss_date(item.get("pubDate", "")),
                            "resumo": descricao[:500],
                            "sentiment": sentiment,
                            "fonte": "rss_noticias",
                            "fonte_especifica": fonte_key,
                            "pais": "BR",
                            "lang": "pt",
                            "created_at": _parse_rss_date(item.get("pubDate", "")),
                        }
                    )
                    matched += 1

        logger.info("RSS '%s': %d/%d artigos com menções", feed_info["nome"], matched, len(items))
        time.sleep(0.5)  # polite delay between feeds

    logger.info("RSS total: %d menções de %d candidatos", len(results), len(candidatos))
    return results


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""
