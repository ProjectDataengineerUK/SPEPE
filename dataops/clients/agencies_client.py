"""Poder360 poll aggregator — única fonte secundária de pesquisas eleitorais.

Substitui scraping individual por agência (Datafolha/Quaest/Ipespe/Paraná/CNT).
O Poder360 já valida e consolida dados de todos os institutos em uma fonte única.

record_confidence_score = 0.82  (curado + validado pelo Poder360)

Estratégias em ordem:
  1. WordPress REST API     /wp-json/wp/v2/posts?categories=<slug>&year=Y
  2. Categoria HTML         /pesquisas-eleitorais/ ou /poder-pesquisas/
  3. Agregador HTML         /agregador-de-pesquisas/ (__NEXT_DATA__ + tabelas)
  4. Artigos individuais    parse de cada artigo encontrado

Thresholds de cross-validation (TSE vs. Poder360):
  ≤ 2 pp → confidence boost para 0.95
  > 5 pp → confidence rebaixado para 0.70
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("spepe.clients.agencies")

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; SPEPE-DataOps/1.0; +https://spepe.com.br)",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
)

_PODER360_BASE = "https://www.poder360.com.br"
_SCRAPE_SLEEP = 0.5
_CONFIDENCE = 0.82

_MATCH_THRESHOLD_PP = 2.0
_DIVERGENCE_THRESHOLD_PP = 5.0
_CONFIDENCE_MATCH = 0.95
_CONFIDENCE_DIVERGE = 0.70

_CANONICAL_COLUMNS: list[str] = [
    "poll_id",
    "instituto",
    "candidato",
    "intencao_pct",
    "rejeicao_pct",
    "data_pesquisa_inicio",
    "data_pesquisa_fim",
    "uf",
    "cd_cargo",
    "n_entrevistados",
    "margem_erro",
    "metodologia",
    "tipo_pesquisa",
    "fonte",
    "record_confidence_score",
    "ano",
]

# Palavras que indicam fragmento de frase (não nome de candidato)
_SENTENCE_WORDS = frozenset(
    {
        "que",
        "com",
        "por",
        "para",
        "são",
        "tem",
        "foi",
        "uma",
        "ante",
        "entre",
        "contra",
        "sobre",
        "como",
        "mais",
        "quando",
        "ainda",
        "apenas",
        "tendo",
        "ficou",
        "marca",
        "lidera",
        "segundo",
    }
)

# Listagem de páginas candidatas a conter pesquisas no Poder360
_LISTING_URLS = [
    f"{_PODER360_BASE}/pesquisas-eleitorais/",
    f"{_PODER360_BASE}/poder-pesquisas/leia-as-ultimas-pesquisas-eleitorais-para-presidente/",
    f"{_PODER360_BASE}/agregador-de-pesquisas/",
    f"{_PODER360_BASE}/pesquisas/",
]

# WP REST API endpoints para custom post types / categorias
_WP_API_PATTERNS = [
    "/wp-json/wp/v2/pesquisas?per_page=50&orderby=date&order=desc&year={year}",
    "/wp-json/wp/v2/posts?per_page=50&orderby=date&order=desc&year={year}&categories_slug=pesquisas-eleitorais",
    "/wp-json/wp/v2/posts?per_page=50&orderby=date&order=desc&year={year}&tags_slug=pesquisa-eleitoral",
    "/wp-json/poder360/v1/pesquisas?ano={year}&cargo={cargo}",
    "/wp-json/poder360/v1/agregador?ano={year}&cargo={cargo}",
]


# ── Utilities ─────────────────────────────────────────────────────────────────


def _empty_canonical() -> pd.DataFrame:
    return pd.DataFrame(columns=_CANONICAL_COLUMNS)


def _is_valid_candidate(name: str) -> bool:
    """Rejeita fragmentos de frase; aceita nomes de candidatos reais."""
    if not name or len(name) < 2 or len(name) > 40:
        return False
    if name.count(" ") > 4:
        return False
    # "Ã" nunca aparece em texto UTF-8 correto — é artefato de mojibake
    if "Ã" in name:
        return False
    words = set(name.lower().split())
    if words & _SENTENCE_WORDS:
        return False
    return True


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
)
def _fetch(url: str, accept_json: bool = False) -> requests.Response:
    headers = {"Accept": "application/json"} if accept_json else {}
    resp = _SESSION.get(url, timeout=30, allow_redirects=True, headers=headers)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp


def _coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ".").str.replace("%", "").str.strip(),
                errors="coerce",
            )
    return df


# ── HTML table parser ─────────────────────────────────────────────────────────


def _parse_tables_from_soup(
    soup: Any,
    year: int,
    cargo: str,
    instituto: str | None = None,
    data_fim: str | None = None,
) -> list[dict]:
    """Extract poll rows from HTML tables in a BeautifulSoup object."""
    rows: list[dict] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True).lower() for td in first_row.find_all("td")]

        cand_idx = next((i for i, h in enumerate(headers) if "candidato" in h or "nome" in h), None)
        pct_idx = next(
            (i for i, h in enumerate(headers) if "%" in h or "inten" in h or "estimul" in h),
            None,
        )
        rej_idx = next((i for i, h in enumerate(headers) if "rejei" in h), None)
        inst_idx = next(
            (i for i, h in enumerate(headers) if "institu" in h or "empresa" in h), None
        )

        if cand_idx is None or pct_idx is None:
            continue

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= max(cand_idx, pct_idx):
                continue

            candidato = cells[cand_idx].strip()
            if not _is_valid_candidate(candidato):
                continue

            pct_raw = cells[pct_idx].replace(",", ".").replace("%", "").strip()
            try:
                intencao = float(pct_raw)
            except ValueError:
                continue
            if not 0 < intencao <= 100:
                continue

            rejeicao = None
            if rej_idx is not None and len(cells) > rej_idx:
                try:
                    rejeicao = float(cells[rej_idx].replace(",", ".").replace("%", "").strip())
                except ValueError:
                    pass

            inst = instituto
            if inst_idx is not None and len(cells) > inst_idx:
                inst = cells[inst_idx].strip() or instituto

            rows.append(
                {
                    "poll_id": None,
                    "instituto": inst,
                    "candidato": candidato,
                    "intencao_pct": intencao,
                    "rejeicao_pct": rejeicao,
                    "data_pesquisa_inicio": None,
                    "data_pesquisa_fim": data_fim,
                    "uf": "BR",
                    "cd_cargo": cargo,
                    "n_entrevistados": None,
                    "margem_erro": None,
                    "metodologia": None,
                    "tipo_pesquisa": "estimulada",
                    "fonte": "poder360",
                    "record_confidence_score": _CONFIDENCE,
                    "ano": year,
                }
            )

    return rows


# ── WP REST API ───────────────────────────────────────────────────────────────


def _fetch_wp_api(year: int, cargo: str) -> list[dict]:
    """Try Poder360 WordPress REST API endpoints — return raw JSON posts list."""
    for pattern in _WP_API_PATTERNS:
        url = _PODER360_BASE + pattern.format(year=year, cargo=cargo)
        try:
            resp = _fetch(url, accept_json=True)
            data = resp.json()
            if isinstance(data, list) and data:
                logger.debug("Poder360 WP API funcionou: %s (%d posts)", url, len(data))
                return data
            if isinstance(data, dict) and (data.get("posts") or data.get("pesquisas")):
                posts = data.get("posts") or data.get("pesquisas") or []
                if posts:
                    return posts
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (404, 403):
                logger.debug("Poder360 WP API: %s → %d", url, exc.response.status_code)
            else:
                logger.debug("Poder360 WP API erro em %s: %s", url, exc)
        except Exception as exc:
            logger.debug("Poder360 WP API: %s → %s", url, exc)

    return []


def _parse_wp_posts(posts: list[dict], year: int, cargo: str) -> list[dict]:
    """Extract poll rows from WordPress REST API post objects."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    rows: list[dict] = []
    year_str = str(year)
    cargo_keywords = {
        "presidente": ["president", "presiden"],
        "governador": ["governad"],
        "senador": ["senador"],
    }
    kws = cargo_keywords.get(cargo.lower(), ["president"])

    for post in posts:
        if not isinstance(post, dict):
            continue

        title = (post.get("title", {}).get("rendered") or "").lower()
        if year_str not in title and year_str not in str(post.get("date", "")):
            continue
        if not any(kw in title for kw in kws):
            continue

        content_html = post.get("content", {}).get("rendered") or ""
        data_fim = post.get("date") or post.get("modified")
        instituto = None

        # Try to extract instituto from title
        for inst in ("datafolha", "quaest", "ipespe", "paraná", "parana", "cnt", "mda", "atlas"):
            if inst in title:
                instituto = inst
                break

        soup = BeautifulSoup(content_html, "html.parser")
        post_rows = _parse_tables_from_soup(soup, year, cargo, instituto, data_fim)
        rows.extend(post_rows)

    return rows


# ── HTML listing + article scraping ───────────────────────────────────────────


def _find_article_links(soup: Any, year: int, cargo: str) -> list[str]:
    """Find individual poll article links from a listing page."""
    cargo_keywords = {
        "presidente": ["presidente", "presidenci", "eleicao", "eleição"],
        "governador": ["governador", "governo"],
        "senador": ["senador"],
    }
    kws = cargo_keywords.get(cargo.lower(), ["presidente"])
    year_str = str(year)
    links: list[str] = []

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        text = (a.get_text(strip=True) + " " + href).lower()
        if year_str not in text and year_str not in href:
            continue
        if not any(kw in text for kw in kws):
            continue
        if not href.startswith("http"):
            href = _PODER360_BASE + href
        if href not in links and "poder360.com.br" in href:
            links.append(href)

    return links[:20]


def _scrape_article(url: str, year: int, cargo: str) -> list[dict]:
    """Fetch a single Poder360 article and extract poll table rows."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    try:
        resp = _fetch(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract institute from <title> or <h1>
        titulo = soup.find("title")
        title_text = titulo.get_text(strip=True).lower() if titulo else ""
        instituto = None
        for inst in ("datafolha", "quaest", "ipespe", "paraná", "parana", "cnt", "mda", "atlas"):
            if inst in title_text:
                instituto = inst
                break

        # Extract date from <time> or meta
        data_fim = None
        time_tag = soup.find("time")
        if time_tag:
            data_fim = time_tag.get("datetime") or time_tag.get_text(strip=True)

        return _parse_tables_from_soup(soup, year, cargo, instituto, data_fim)
    except Exception as exc:
        logger.debug("Poder360 artigo %s: %s", url, exc)
        return []


def _parse_next_data(soup: Any, year: int, cargo: str) -> list[dict]:
    """Try __NEXT_DATA__ JSON embedded in page (Next.js)."""
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return []
    try:
        data = json.loads(tag.string or "")
    except (json.JSONDecodeError, AttributeError):
        return []

    page_props = data.get("props", {}).get("pageProps", {})
    polls_raw = (
        page_props.get("polls")
        or page_props.get("pesquisas")
        or page_props.get("data", {}).get("pesquisas", [])
        or []
    )
    if not polls_raw or not isinstance(polls_raw, list):
        return []

    rows: list[dict] = []
    for p in polls_raw:
        if not isinstance(p, dict):
            continue
        data_fim = p.get("data_fim") or p.get("dataFim") or p.get("date")
        if data_fim and str(year) not in str(data_fim):
            continue
        base = {
            "poll_id": p.get("id") or p.get("registro"),
            "instituto": p.get("instituto") or p.get("institute"),
            "data_pesquisa_inicio": p.get("data_inicio") or p.get("dataInicio"),
            "data_pesquisa_fim": data_fim,
            "n_entrevistados": p.get("n_entrevistados") or p.get("amostra"),
            "margem_erro": p.get("margem_erro"),
            "uf": p.get("uf", "BR"),
            "cd_cargo": cargo,
            "metodologia": None,
            "tipo_pesquisa": "estimulada",
            "fonte": "poder360",
            "record_confidence_score": _CONFIDENCE,
            "ano": year,
        }
        for c in p.get("candidatos") or p.get("resultados") or []:
            candidato = c.get("nome") or c.get("candidato") or c.get("name") or ""
            if not _is_valid_candidate(candidato):
                continue
            try:
                intencao = float(
                    str(c.get("intencao_pct") or c.get("intencao") or 0).replace(",", ".")
                )
            except (ValueError, TypeError):
                continue
            rows.append(
                {
                    **base,
                    "candidato": candidato,
                    "intencao_pct": intencao,
                    "rejeicao_pct": c.get("rejeicao"),
                }
            )

    return rows


# ── Main aggregator function ──────────────────────────────────────────────────


def fetch_poder360_aggregator(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Fetch poll data from Poder360 aggregator.

    Tries in order:
      1. WordPress REST API (structured JSON)
      2. Listing page HTML → article links → parse each article
      3. Aggregator page HTML (__NEXT_DATA__ + tables)

    Returns DataFrame with _CANONICAL_COLUMNS schema.
    record_confidence_score = 0.82.
    Returns empty DataFrame on complete failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — fetch_poder360_aggregator desabilitado")
        return _empty_canonical()

    rows: list[dict] = []

    # ── Strategy 1: WP REST API ───────────────────────────────────────────────
    posts = _fetch_wp_api(year, cargo)
    if posts:
        rows = _parse_wp_posts(posts, year, cargo)
        if rows:
            logger.debug("Poder360: WP REST API → %d linhas", len(rows))

    # ── Strategy 2: listing page → individual articles ────────────────────────
    if not rows:
        article_links: list[str] = []
        for listing_url in _LISTING_URLS[:2]:  # só as páginas de listagem
            try:
                resp = _fetch(listing_url)
                soup = BeautifulSoup(resp.text, "html.parser")
                found = _find_article_links(soup, year, cargo)
                article_links.extend(lnk for lnk in found if lnk not in article_links)
                if article_links:
                    break
            except Exception as exc:
                logger.debug("Poder360 listing %s: %s", listing_url, exc)

        for url in article_links[:15]:
            time.sleep(_SCRAPE_SLEEP)
            rows.extend(_scrape_article(url, year, cargo))

        if rows:
            logger.debug("Poder360: artigos → %d linhas", len(rows))

    # ── Strategy 3: aggregator page (__NEXT_DATA__ + tables) ─────────────────
    if not rows:
        try:
            agg_url = f"{_PODER360_BASE}/agregador-de-pesquisas/?cargo={cargo}&ano={year}"
            resp = _fetch(agg_url)
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = _parse_next_data(soup, year, cargo) or _parse_tables_from_soup(soup, year, cargo)
            if rows:
                logger.debug("Poder360: agregador page → %d linhas", len(rows))
        except Exception as exc:
            logger.debug("Poder360 agregador page: %s", exc)

    if not rows:
        logger.warning("fetch_poder360_aggregator: sem dados para ano=%d cargo=%s", year, cargo)
        return _empty_canonical()

    df = pd.DataFrame(rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df = _coerce_numeric(df, ["intencao_pct", "rejeicao_pct", "n_entrevistados", "margem_erro"])

    if "data_pesquisa_fim" in df.columns:
        year_mask = df["data_pesquisa_fim"].dt.year == year
        if year_mask.any():
            df = df[year_mask | df["data_pesquisa_fim"].isna()]

    logger.info("Poder360 agregador: %d linhas para ano=%d cargo=%s", len(df), year, cargo)

    if set(_CANONICAL_COLUMNS).issubset(df.columns):
        return df[_CANONICAL_COLUMNS].copy()
    # Add missing columns with None
    for col in _CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[_CANONICAL_COLUMNS].copy()


# ── Public interface (mantém compatibilidade com pesquisas_ingest_job.py) ─────


def fetch_all_agencies(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Fetch poll data from Poder360 aggregator (substitui 5 scrapers individuais).

    Retorna DataFrame com schema canonical. record_confidence_score = 0.82.
    """
    return fetch_poder360_aggregator(year, cargo)


def cross_validate_with_agencies(df_tse: pd.DataFrame, year: int, cargo: str) -> pd.DataFrame:
    """Cross-validate TSE poll data against Poder360 aggregator.

    Matching key: (instituto_normalized, candidato_normalized) within ±3-day window.
    ≤ 2 pp → boost confidence to 0.95
    > 5 pp → degrade confidence to 0.70
    """
    if df_tse.empty:
        return df_tse
    if "intencao_pct" not in df_tse.columns or "record_confidence_score" not in df_tse.columns:
        logger.warning("cross_validate: colunas obrigatórias ausentes em df_tse")
        return df_tse

    df_ref = fetch_poder360_aggregator(year, cargo)
    if df_ref.empty:
        logger.info("cross_validate: Poder360 sem dados — df_tse inalterado")
        return df_tse

    # Build lookup: (instituto_norm, candidato_norm) → [(data_fim, intencao_pct)]
    lookup: dict[tuple[str, str], list[tuple[Any, float]]] = {}
    for _, row in df_ref.iterrows():
        if pd.isna(row.get("intencao_pct")):
            continue
        key = (_normalize_name(row.get("instituto")), _normalize_name(row.get("candidato")))
        lookup.setdefault(key, []).append(
            (row.get("data_pesquisa_fim"), float(row["intencao_pct"]))
        )

    df_out = df_tse.copy()
    matched = boosted = degraded = 0

    for idx, tse_row in df_out.iterrows():
        if pd.isna(tse_row.get("intencao_pct")):
            continue
        key = (_normalize_name(tse_row.get("instituto")), _normalize_name(tse_row.get("candidato")))
        entries = lookup.get(key)
        if not entries:
            continue

        tse_pct = float(tse_row["intencao_pct"])
        tse_date = tse_row.get("data_pesquisa_fim")
        best_diff = _best_match_diff(entries, tse_pct, tse_date)
        if best_diff is None:
            continue

        matched += 1
        if best_diff <= _MATCH_THRESHOLD_PP:
            df_out.at[idx, "record_confidence_score"] = _CONFIDENCE_MATCH
            boosted += 1
        elif best_diff > _DIVERGENCE_THRESHOLD_PP:
            df_out.at[idx, "record_confidence_score"] = _CONFIDENCE_DIVERGE
            degraded += 1
            logger.warning(
                "cross_validate: divergência %.1fpp — %s/%s (TSE=%.1f%%)",
                best_diff,
                tse_row.get("instituto"),
                tse_row.get("candidato"),
                tse_pct,
            )

    logger.info(
        "cross_validate: %d matches — %d boost (→0.95), %d rebaixados (→0.70)",
        matched,
        boosted,
        degraded,
    )
    return df_out


def _best_match_diff(
    entries: list[tuple[Any, float]], tse_pct: float, tse_date: Any
) -> float | None:
    """Return smallest |tse_pct - ref_pct| within ±3-day date window."""
    best: float | None = None
    for ref_date, ref_pct in entries:
        if tse_date is not None and not pd.isna(tse_date):
            if ref_date is not None and not pd.isna(ref_date):
                try:
                    if abs((pd.Timestamp(tse_date) - pd.Timestamp(ref_date)).days) > 3:
                        continue
                except Exception:
                    pass
        diff = abs(tse_pct - ref_pct)
        if best is None or diff < best:
            best = diff
    return best
