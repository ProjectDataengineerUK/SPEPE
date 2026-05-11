"""Agencies client — scraping de agências de pesquisa como fontes secundárias.

Hierarquia de confiança:
  TSE PesqEle PDF (Gemini Flash) + match agência → 0.95
  TSE PesqEle PDF (Gemini Flash) sozinho         → 0.85
  Agência direta (Datafolha, Quaest)              → 0.90
  Agência direta (Ipespe, Paraná, CNT)            → 0.85–0.88
  Atlas Político scraping                         → 0.75
  Poder360 scraping                               → 0.70
  TSE PDF pdfplumber                              → 0.70
  TSE PDF falhou / estimativa                     → 0.30

Uso:
  df_tse = fetch_pesqele_csv(year) → enrich_with_pdfs(df_tse)
  df_validated = cross_validate_with_agencies(df_tse, year, cargo)
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
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

_DATAFOLHA_BASE = "https://datafolha.folha.uol.com.br/eleicoes/"
_QUAEST_BASE = "https://quaest.com.br/pesquisas-eleitorais/"
_IPESPE_BASE = "https://www.ipespe.com.br/pesquisas/"
_PARANA_URLS = [
    "https://www.paranápesquisas.com.br/pesquisas-eleitorais/",
    "https://paranápesquisas.com.br/pesquisas-eleitorais/",
    "https://www.paranápesquisas.com.br/pesquisas/",
]
_CNT_URLS = [
    "https://cnt.org.br/pesquisas",
    "https://cnt.org.br/pesquisas-transporte",
]
_SCRAPE_SLEEP = 0.5

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

# Cross-validation thresholds (percentage points)
_MATCH_THRESHOLD_PP = 2.0
_DIVERGENCE_THRESHOLD_PP = 5.0

_CONFIDENCE_MATCH = 0.95
_CONFIDENCE_DIVERGE = 0.70
_CONFIDENCE_DATAFOLHA = 0.90
_CONFIDENCE_QUAEST = 0.90
_CONFIDENCE_IPESPE = 0.88
_CONFIDENCE_IPESPE_FAIL = 0.30
_CONFIDENCE_PARANA = 0.85
_CONFIDENCE_CNT = 0.85


# ── Utilities ──────────────────────────────────────────────────────────────────


def _normalize_col(col: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", col.strip().lower())


def _empty_canonical() -> pd.DataFrame:
    return pd.DataFrame(columns=_CANONICAL_COLUMNS)


def _coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ".").str.replace("%", "").str.strip(),
                errors="coerce",
            )
    return df


def _normalize_instituto(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


# ── Datafolha ─────────────────────────────────────────────────────────────────


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
)
def _fetch_datafolha_page(url: str) -> requests.Response:
    resp = _SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp


def _datafolha_parse_poll_page(
    soup: Any, poll_url: str, year: int, cargo: str
) -> list[dict]:
    """Extract voting intention rows from a single Datafolha poll page."""
    rows: list[dict] = []

    base: dict = {
        "poll_id": None,
        "instituto": "datafolha",
        "uf": "BR",
        "cd_cargo": cargo,
        "metodologia": None,
        "tipo_pesquisa": "estimulada",
        "fonte": "datafolha",
        "record_confidence_score": _CONFIDENCE_DATAFOLHA,
        "ano": year,
        "data_pesquisa_inicio": None,
        "data_pesquisa_fim": None,
        "n_entrevistados": None,
        "margem_erro": None,
        "rejeicao_pct": None,
    }

    # Extract poll date from meta or title
    date_tag = soup.find("time") or soup.find(class_=re.compile(r"date|data"))
    if date_tag:
        base["data_pesquisa_fim"] = date_tag.get("datetime") or date_tag.get_text(strip=True)

    # Extract methodology / sample size from body text
    body_text = soup.get_text(" ", strip=True)
    n_match = re.search(r"(\d[\d.]+)\s*entrevistados?", body_text, re.IGNORECASE)
    if n_match:
        base["n_entrevistados"] = float(n_match.group(1).replace(".", ""))

    margem_match = re.search(r"margem.*?(\d[\d,]+)\s*pontos?", body_text, re.IGNORECASE)
    if margem_match:
        base["margem_erro"] = float(margem_match.group(1).replace(",", "."))

    # Strategy 1: look for structured HTML tables with candidato + % columns
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers:
            # Some Datafolha tables use <td> in first row as headers
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True).lower() for td in first_row.find_all("td")]

        candidato_idx = next(
            (i for i, h in enumerate(headers) if "candidato" in h or "nome" in h), None
        )
        pct_idx = next(
            (i for i, h in enumerate(headers) if "%" in h or "inten" in h or "estimul" in h),
            None,
        )
        rejeicao_idx = next(
            (i for i, h in enumerate(headers) if "rejei" in h), None
        )

        if candidato_idx is None or pct_idx is None:
            continue

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= max(candidato_idx, pct_idx):
                continue
            candidato = cells[candidato_idx].strip()
            if not candidato:
                continue
            pct_raw = cells[pct_idx].replace(",", ".").replace("%", "").strip()
            try:
                intencao = float(pct_raw)
            except ValueError:
                continue
            rejeicao = None
            if rejeicao_idx is not None and len(cells) > rejeicao_idx:
                try:
                    rejeicao = float(
                        cells[rejeicao_idx].replace(",", ".").replace("%", "").strip()
                    )
                except ValueError:
                    pass
            rows.append(
                {
                    **base,
                    "candidato": candidato,
                    "intencao_pct": intencao,
                    "rejeicao_pct": rejeicao,
                }
            )

    # Strategy 2: look for inline data patterns like "Candidato X: 35%"
    if not rows:
        for match in re.finditer(
            r"([A-ZÁÉÍÓÚÃÕÂÊÔÀÜ][^\n:]{2,40})[\s:]+(\d{1,3}(?:[.,]\d)?)\s*%",
            body_text,
        ):
            candidato = match.group(1).strip()
            if len(candidato) < 3 or any(
                skip in candidato.lower()
                for skip in ("brancos", "nulos", "ns", "nr", "total", "outros")
            ):
                continue
            try:
                intencao = float(match.group(2).replace(",", "."))
            except ValueError:
                continue
            rows.append(
                {
                    **base,
                    "candidato": candidato,
                    "intencao_pct": intencao,
                }
            )

    return rows


def _datafolha_find_poll_links(soup: Any, year: int, cargo: str) -> list[str]:
    """Return URLs of individual poll pages from the Datafolha listing page."""
    links: list[str] = []
    cargo_keywords = {
        "presidente": ["presidente", "presidencial"],
        "governador": ["governador", "governatorial"],
        "senador": ["senador"],
    }
    keywords = cargo_keywords.get(cargo.lower(), ["presidente"])

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text(strip=True) + " " + href).lower()
        if str(year) not in text and str(year) not in href:
            continue
        if not any(kw in text for kw in keywords):
            continue
        if not href.startswith("http"):
            href = "https://datafolha.folha.uol.com.br" + href
        if href not in links:
            links.append(href)

    return links[:20]  # cap at 20 poll pages per run


def scrape_datafolha(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape voting intention data from Datafolha public website.

    Fetches the elections index page, discovers poll links for the given
    year/cargo, then visits each to extract voting intention tables.
    record_confidence_score = 0.90.

    Returns an empty DataFrame on any scraping failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — scrape_datafolha desabilitado")
        return _empty_canonical()

    try:
        resp = _fetch_datafolha_page(_DATAFOLHA_BASE)
    except Exception as exc:
        logger.warning("Datafolha: falha ao buscar índice: %s", exc)
        return _empty_canonical()

    soup = BeautifulSoup(resp.text, "html.parser")
    poll_urls = _datafolha_find_poll_links(soup, year, cargo)

    if not poll_urls:
        logger.warning(
            "Datafolha: nenhum link de pesquisa encontrado para ano=%d cargo=%s", year, cargo
        )
        return _empty_canonical()

    all_rows: list[dict] = []
    for url in poll_urls:
        try:
            resp_poll = _fetch_datafolha_page(url)
            poll_soup = BeautifulSoup(resp_poll.text, "html.parser")
            poll_rows = _datafolha_parse_poll_page(poll_soup, url, year, cargo)
            all_rows.extend(poll_rows)
        except Exception as exc:
            logger.warning("Datafolha: falha ao parsear %s: %s", url, exc)

    if not all_rows:
        logger.warning(
            "Datafolha: scraping retornou DataFrame vazio para ano=%d cargo=%s", year, cargo
        )
        return _empty_canonical()

    df = pd.DataFrame(all_rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df = _coerce_numeric(df, ["intencao_pct", "rejeicao_pct", "n_entrevistados", "margem_erro"])

    # Deduplicate by (candidato, data_pesquisa_fim)
    if "candidato" in df.columns and "data_pesquisa_fim" in df.columns:
        df = df.drop_duplicates(subset=["candidato", "data_pesquisa_fim"])

    logger.info(
        "Datafolha: %d linhas scrapeadas para ano=%d cargo=%s", len(df), year, cargo
    )
    return df[_CANONICAL_COLUMNS].copy() if set(_CANONICAL_COLUMNS).issubset(df.columns) else df


# ── Quaest ────────────────────────────────────────────────────────────────────


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
)
def _fetch_quaest_page(url: str) -> requests.Response:
    resp = _SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp


def _quaest_parse_next_data(
    soup: Any, year: int, cargo: str
) -> list[dict]:
    """Strategy 1: parse __NEXT_DATA__ JSON (Quaest is Next.js)."""
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return []
    try:
        next_data = json.loads(tag.string or "")
    except (json.JSONDecodeError, AttributeError):
        return []

    page_props = next_data.get("props", {}).get("pageProps", {})
    pesquisas_raw = (
        page_props.get("pesquisas")
        or page_props.get("polls")
        or page_props.get("data", {}).get("pesquisas", [])
        or page_props.get("results", [])
        or []
    )

    if not pesquisas_raw or not isinstance(pesquisas_raw, list):
        # Try deeper nesting
        for key, val in page_props.items():
            if isinstance(val, dict):
                nested = val.get("pesquisas") or val.get("polls") or []
                if nested and isinstance(nested, list):
                    pesquisas_raw = nested
                    break

    rows: list[dict] = []
    for p in pesquisas_raw:
        if not isinstance(p, dict):
            continue
        data_fim = p.get("data_fim") or p.get("dataFim") or p.get("end_date") or p.get("date")
        data_ini = p.get("data_inicio") or p.get("dataInicio") or p.get("start_date")

        # Filter by year
        if data_fim and str(year) not in str(data_fim) and str(year) not in str(data_ini or ""):
            continue

        base: dict = {
            "poll_id": p.get("id") or p.get("registro") or p.get("slug"),
            "instituto": "quaest",
            "data_pesquisa_inicio": data_ini,
            "data_pesquisa_fim": data_fim,
            "uf": p.get("uf") or p.get("estado", "BR"),
            "cd_cargo": cargo,
            "n_entrevistados": p.get("n_entrevistados") or p.get("amostra") or p.get("sample"),
            "margem_erro": p.get("margem_erro") or p.get("margin"),
            "metodologia": p.get("metodologia") or p.get("method"),
            "tipo_pesquisa": "estimulada",
            "fonte": "quaest",
            "record_confidence_score": _CONFIDENCE_QUAEST,
            "ano": year,
            "rejeicao_pct": None,
        }

        candidatos = (
            p.get("candidatos")
            or p.get("resultados")
            or p.get("candidates")
            or p.get("results")
            or []
        )
        if candidatos:
            for c in candidatos:
                rows.append(
                    {
                        **base,
                        "candidato": c.get("nome") or c.get("candidato") or c.get("name"),
                        "intencao_pct": c.get("intencao_pct")
                        or c.get("intencao")
                        or c.get("percentual")
                        or c.get("value"),
                        "rejeicao_pct": c.get("rejeicao") or c.get("rejection"),
                    }
                )
        else:
            rows.append({**base, "candidato": None, "intencao_pct": None})

    return rows


def _quaest_parse_inline_json(soup: Any, year: int, cargo: str) -> list[dict]:
    """Strategy 2: look for inline window.__STATE__ or similar JSON blobs."""
    _PATTERNS = [
        re.compile(r"window\.__(?:INITIAL_STATE|DATA|APP_STATE|REDUX_STATE)__\s*=\s*(\{.*?\});", re.DOTALL),
        re.compile(r"var\s+(?:initialData|pesquisasData|pollsData)\s*=\s*(\[.*?\]);", re.DOTALL),
    ]
    for script in soup.find_all("script"):
        text = script.string or ""
        for pattern in _PATTERNS:
            m = pattern.search(text)
            if not m:
                continue
            try:
                blob = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            pesquisas_raw = (
                blob.get("pesquisas")
                or blob.get("polls")
                or (blob if isinstance(blob, list) else None)
                or []
            )
            if not pesquisas_raw:
                continue
            rows: list[dict] = []
            for p in pesquisas_raw:
                if not isinstance(p, dict):
                    continue
                data_fim = p.get("data_fim") or p.get("dataFim") or p.get("date")
                if data_fim and str(year) not in str(data_fim):
                    continue
                base: dict = {
                    "poll_id": p.get("id") or p.get("registro"),
                    "instituto": "quaest",
                    "data_pesquisa_inicio": p.get("data_inicio") or p.get("dataInicio"),
                    "data_pesquisa_fim": data_fim,
                    "uf": p.get("uf", "BR"),
                    "cd_cargo": cargo,
                    "n_entrevistados": p.get("n_entrevistados") or p.get("amostra"),
                    "margem_erro": p.get("margem_erro"),
                    "metodologia": p.get("metodologia"),
                    "tipo_pesquisa": "estimulada",
                    "fonte": "quaest",
                    "record_confidence_score": _CONFIDENCE_QUAEST,
                    "ano": year,
                    "rejeicao_pct": None,
                }
                for c in (p.get("candidatos") or p.get("resultados") or []):
                    rows.append(
                        {
                            **base,
                            "candidato": c.get("nome") or c.get("candidato"),
                            "intencao_pct": c.get("intencao_pct") or c.get("intencao") or c.get("percentual"),
                            "rejeicao_pct": c.get("rejeicao"),
                        }
                    )
            if rows:
                return rows
    return []


def _quaest_parse_html_tables(soup: Any, year: int, cargo: str) -> list[dict]:
    """Strategy 3: raw HTML table fallback."""
    rows: list[dict] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True).lower() for td in first_row.find_all("td")]

        candidato_idx = next(
            (i for i, h in enumerate(headers) if "candidato" in h or "nome" in h), None
        )
        pct_idx = next(
            (i for i, h in enumerate(headers) if "%" in h or "inten" in h or "estimul" in h),
            None,
        )
        if candidato_idx is None or pct_idx is None:
            continue

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= max(candidato_idx, pct_idx):
                continue
            candidato = cells[candidato_idx].strip()
            if not candidato:
                continue
            pct_raw = cells[pct_idx].replace(",", ".").replace("%", "").strip()
            try:
                intencao = float(pct_raw)
            except ValueError:
                continue
            rows.append(
                {
                    "poll_id": None,
                    "instituto": "quaest",
                    "candidato": candidato,
                    "intencao_pct": intencao,
                    "rejeicao_pct": None,
                    "data_pesquisa_inicio": None,
                    "data_pesquisa_fim": None,
                    "uf": "BR",
                    "cd_cargo": cargo,
                    "n_entrevistados": None,
                    "margem_erro": None,
                    "metodologia": None,
                    "tipo_pesquisa": "estimulada",
                    "fonte": "quaest",
                    "record_confidence_score": _CONFIDENCE_QUAEST,
                    "ano": year,
                }
            )
    return rows


def scrape_quaest(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape voting intention data from Quaest public website.

    Tries three strategies in order:
      1. Parse __NEXT_DATA__ JSON (site is Next.js)
      2. Look for inline JS data blobs
      3. Parse HTML tables

    record_confidence_score = 0.90.
    Returns an empty DataFrame on any scraping failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — scrape_quaest desabilitado")
        return _empty_canonical()

    try:
        resp = _fetch_quaest_page(_QUAEST_BASE)
    except Exception as exc:
        logger.warning("Quaest: falha ao buscar página: %s", exc)
        return _empty_canonical()

    soup = BeautifulSoup(resp.text, "html.parser")

    rows = (
        _quaest_parse_next_data(soup, year, cargo)
        or _quaest_parse_inline_json(soup, year, cargo)
        or _quaest_parse_html_tables(soup, year, cargo)
    )

    if not rows:
        logger.warning(
            "Quaest: scraping retornou DataFrame vazio para ano=%d cargo=%s", year, cargo
        )
        return _empty_canonical()

    df = pd.DataFrame(rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df = _coerce_numeric(df, ["intencao_pct", "rejeicao_pct", "n_entrevistados", "margem_erro"])

    # Filter to target year when dates resolved
    if "data_pesquisa_fim" in df.columns:
        year_mask = df["data_pesquisa_fim"].dt.year == year
        if year_mask.any():
            df = df[year_mask | df["data_pesquisa_fim"].isna()]

    logger.info(
        "Quaest: %d linhas scrapeadas para ano=%d cargo=%s", len(df), year, cargo
    )
    return df[_CANONICAL_COLUMNS].copy() if set(_CANONICAL_COLUMNS).issubset(df.columns) else df


# ── PDF table extractor (shared by Ipespe + CNT) ─────────────────────────────


def _extract_pdf_tables_raw(pdf_bytes: bytes) -> list[dict]:
    """Extract candidato/intencao_pct rows from a PDF using pdfplumber.

    Returns list of raw dicts with keys ``candidato`` and ``intencao_pct``.
    Returns empty list on any failure, including missing pdfplumber.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber não instalado — extração de PDF desabilitada")
        return []

    rows: list[dict] = []
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if not table or len(table) < 2:
                        continue
                    header = [str(c or "").lower() for c in table[0]]
                    candidato_idx = next(
                        (i for i, h in enumerate(header) if "candidato" in h or "nome" in h), None
                    )
                    intencao_idx = next(
                        (i for i, h in enumerate(header) if "inten" in h or "%" in h), None
                    )
                    if candidato_idx is None or intencao_idx is None:
                        continue
                    for row in table[1:]:
                        if not row or len(row) <= max(candidato_idx, intencao_idx):
                            continue
                        candidato = str(row[candidato_idx] or "").strip()
                        if not candidato:
                            continue
                        raw = str(row[intencao_idx] or "").replace(",", ".").replace("%", "").strip()
                        try:
                            intencao = float(raw)
                        except ValueError:
                            intencao = None
                        rows.append({"candidato": candidato, "intencao_pct": intencao})
    except Exception as exc:
        logger.debug("pdfplumber erro: %s", exc)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return rows


# ── Ipespe ────────────────────────────────────────────────────────────────────


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
)
def _fetch_ipespe_page(url: str) -> requests.Response:
    resp = _SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp


def scrape_ipespe(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape Ipespe via listagem de PDFs → extração com pdfplumber.

    Linhas onde pdfplumber não extrai tabelas preservam metadados com
    intencao_pct=None e record_confidence_score=0.30 para rastreabilidade.
    Returns an empty DataFrame on any listing failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — scrape_ipespe desabilitado")
        return _empty_canonical()

    try:
        resp = _fetch_ipespe_page(_IPESPE_BASE)
    except Exception as exc:
        logger.warning("Ipespe: falha ao buscar listagem: %s", exc)
        return _empty_canonical()

    soup = BeautifulSoup(resp.text, "html.parser")
    year_str = str(year)

    pdf_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        text = a.get_text(strip=True) + href
        if href.lower().endswith(".pdf") and year_str in text:
            if not href.startswith("http"):
                href = "https://www.ipespe.com.br" + href
            if href not in pdf_links:
                pdf_links.append(href)

    pdf_links = pdf_links[:10]

    if not pdf_links:
        logger.warning("Ipespe: nenhum PDF encontrado para ano=%d", year)
        return _empty_canonical()

    all_rows: list[dict] = []
    for pdf_url in pdf_links:
        try:
            time.sleep(_SCRAPE_SLEEP)
            r = _fetch_ipespe_page(pdf_url)
            pdf_rows = _extract_pdf_tables_raw(r.content)
            if pdf_rows:
                for row in pdf_rows:
                    all_rows.append(
                        {
                            "poll_id": None,
                            "instituto": "ipespe",
                            "candidato": row["candidato"],
                            "intencao_pct": row["intencao_pct"],
                            "rejeicao_pct": None,
                            "data_pesquisa_inicio": None,
                            "data_pesquisa_fim": None,
                            "uf": "BR",
                            "cd_cargo": cargo,
                            "n_entrevistados": None,
                            "margem_erro": None,
                            "metodologia": None,
                            "tipo_pesquisa": "estimulada",
                            "fonte": "ipespe",
                            "record_confidence_score": _CONFIDENCE_IPESPE,
                            "ano": year,
                        }
                    )
            else:
                # Preserve failure row for audit
                all_rows.append(
                    {
                        "poll_id": None,
                        "instituto": "ipespe",
                        "candidato": None,
                        "intencao_pct": None,
                        "rejeicao_pct": None,
                        "data_pesquisa_inicio": None,
                        "data_pesquisa_fim": None,
                        "uf": "BR",
                        "cd_cargo": cargo,
                        "n_entrevistados": None,
                        "margem_erro": None,
                        "metodologia": None,
                        "tipo_pesquisa": None,
                        "fonte": "ipespe",
                        "record_confidence_score": _CONFIDENCE_IPESPE_FAIL,
                        "ano": year,
                    }
                )
                logger.debug("Ipespe: pdfplumber sem tabelas em %s", pdf_url)
        except Exception as exc:
            logger.debug("Ipespe: falha ao processar %s: %s", pdf_url, exc)

    if not all_rows:
        logger.warning("Ipespe: nenhum dado extraído para ano=%d cargo=%s", year, cargo)
        return _empty_canonical()

    df = pd.DataFrame(all_rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df = _coerce_numeric(df, ["intencao_pct", "rejeicao_pct", "n_entrevistados", "margem_erro"])
    logger.info("Ipespe: %d linhas para ano=%d cargo=%s", len(df), year, cargo)
    return df[_CANONICAL_COLUMNS].copy() if set(_CANONICAL_COLUMNS).issubset(df.columns) else df


# ── Paraná Pesquisas ──────────────────────────────────────────────────────────


def _try_fetch(urls: list[str]) -> requests.Response | None:
    """Try multiple URLs in order; return first successful response or None."""
    for url in urls:
        try:
            resp = _SESSION.get(url, timeout=30)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            logger.debug("_try_fetch: %s falhou: %s", url, exc)
    return None


def _parse_html_tables_generic(
    soup: Any,
    year: int,
    fonte: str,
    instituto: str,
    cargo: str,
    confidence: float,
) -> list[dict]:
    rows: list[dict] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True).lower() for td in first_row.find_all("td")]
        candidato_idx = next(
            (i for i, h in enumerate(headers) if "candidato" in h or "nome" in h), None
        )
        pct_idx = next(
            (i for i, h in enumerate(headers) if "%" in h or "inten" in h or "voto" in h), None
        )
        rejeicao_idx = next(
            (i for i, h in enumerate(headers) if "rejei" in h), None
        )
        if candidato_idx is None or pct_idx is None:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= max(candidato_idx, pct_idx):
                continue
            candidato = cells[candidato_idx].strip()
            if not candidato:
                continue
            pct_raw = cells[pct_idx].replace(",", ".").replace("%", "").strip()
            try:
                intencao = float(pct_raw)
            except ValueError:
                continue
            rejeicao = None
            if rejeicao_idx is not None and len(cells) > rejeicao_idx:
                try:
                    rejeicao = float(cells[rejeicao_idx].replace(",", ".").replace("%", "").strip())
                except ValueError:
                    pass
            rows.append(
                {
                    "poll_id": None,
                    "instituto": instituto,
                    "candidato": candidato,
                    "intencao_pct": intencao,
                    "rejeicao_pct": rejeicao,
                    "data_pesquisa_inicio": None,
                    "data_pesquisa_fim": None,
                    "uf": "BR",
                    "cd_cargo": cargo,
                    "n_entrevistados": None,
                    "margem_erro": None,
                    "metodologia": None,
                    "tipo_pesquisa": "estimulada",
                    "fonte": fonte,
                    "record_confidence_score": confidence,
                    "ano": year,
                }
            )
    return rows


def scrape_parana_pesquisas(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape Paraná Pesquisas via tabelas HTML; tenta múltiplas URLs.

    record_confidence_score = 0.85.
    Returns an empty DataFrame on any scraping failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — scrape_parana_pesquisas desabilitado")
        return _empty_canonical()

    resp = _try_fetch(_PARANA_URLS)
    if resp is None:
        logger.warning("Paraná Pesquisas: todas as URLs falharam para ano=%d", year)
        return _empty_canonical()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = _parse_html_tables_generic(
        soup, year, "parana_pesquisas", "parana_pesquisas", cargo, _CONFIDENCE_PARANA
    )

    year_str = str(year)
    article_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        text = a.get_text(strip=True) + href
        if year_str in text and ("pesquisa" in text.lower() or "elei" in text.lower()):
            if not href.startswith("http"):
                href = "https://www.paranápesquisas.com.br" + href
            if href not in article_links:
                article_links.append(href)

    for link in article_links[:10]:
        try:
            time.sleep(_SCRAPE_SLEEP)
            r = _SESSION.get(link, timeout=30)
            r.raise_for_status()
            page_soup = BeautifulSoup(r.text, "html.parser")
            rows.extend(
                _parse_html_tables_generic(
                    page_soup, year, "parana_pesquisas", "parana_pesquisas", cargo, _CONFIDENCE_PARANA
                )
            )
        except Exception as exc:
            logger.debug("Paraná Pesquisas: falha em %s: %s", link, exc)

    if not rows:
        logger.warning(
            "Paraná Pesquisas: scraping retornou DataFrame vazio para ano=%d cargo=%s", year, cargo
        )
        return _empty_canonical()

    df = pd.DataFrame(rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df = _coerce_numeric(df, ["intencao_pct", "rejeicao_pct", "n_entrevistados", "margem_erro"])
    logger.info("Paraná Pesquisas: %d linhas para ano=%d cargo=%s", len(df), year, cargo)
    return df[_CANONICAL_COLUMNS].copy() if set(_CANONICAL_COLUMNS).issubset(df.columns) else df


# ── CNT/MDA ───────────────────────────────────────────────────────────────────


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
)
def _fetch_cnt_page(url: str) -> requests.Response:
    resp = _SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp


def scrape_cnt_mda(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape CNT/MDA via listagem de PDFs → extração com pdfplumber.

    record_confidence_score = 0.85.
    Returns an empty DataFrame on any listing or download failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — scrape_cnt_mda desabilitado")
        return _empty_canonical()

    resp = None
    for url in _CNT_URLS:
        try:
            resp = _fetch_cnt_page(url)
            break
        except Exception as exc:
            logger.debug("CNT/MDA: %s falhou: %s", url, exc)

    if resp is None:
        logger.warning("CNT/MDA: todas as URLs falharam para ano=%d", year)
        return _empty_canonical()

    soup = BeautifulSoup(resp.text, "html.parser")
    year_str = str(year)

    pdf_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        text = a.get_text(strip=True) + href
        if href.lower().endswith(".pdf") and year_str in text:
            if not href.startswith("http"):
                href = "https://cnt.org.br" + href
            if href not in pdf_links:
                pdf_links.append(href)

    pdf_links = pdf_links[:10]

    if not pdf_links:
        logger.warning("CNT/MDA: nenhum PDF encontrado para ano=%d", year)
        return _empty_canonical()

    all_rows: list[dict] = []
    for pdf_url in pdf_links:
        try:
            time.sleep(_SCRAPE_SLEEP)
            r = _fetch_cnt_page(pdf_url)
            pdf_rows = _extract_pdf_tables_raw(r.content)
            for row in pdf_rows:
                all_rows.append(
                    {
                        "poll_id": None,
                        "instituto": "cnt_mda",
                        "candidato": row["candidato"],
                        "intencao_pct": row["intencao_pct"],
                        "rejeicao_pct": None,
                        "data_pesquisa_inicio": None,
                        "data_pesquisa_fim": None,
                        "uf": "BR",
                        "cd_cargo": cargo,
                        "n_entrevistados": None,
                        "margem_erro": None,
                        "metodologia": None,
                        "tipo_pesquisa": "estimulada",
                        "fonte": "cnt_mda",
                        "record_confidence_score": _CONFIDENCE_CNT,
                        "ano": year,
                    }
                )
            if not pdf_rows:
                logger.debug("CNT/MDA: pdfplumber sem tabelas em %s", pdf_url)
        except Exception as exc:
            logger.debug("CNT/MDA: falha ao processar %s: %s", pdf_url, exc)

    if not all_rows:
        logger.warning("CNT/MDA: nenhum dado extraído para ano=%d cargo=%s", year, cargo)
        return _empty_canonical()

    df = pd.DataFrame(all_rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df = _coerce_numeric(df, ["intencao_pct", "rejeicao_pct", "n_entrevistados", "margem_erro"])
    logger.info("CNT/MDA: %d linhas para ano=%d cargo=%s", len(df), year, cargo)
    return df[_CANONICAL_COLUMNS].copy() if set(_CANONICAL_COLUMNS).issubset(df.columns) else df


# ── fetch_all_agencies ────────────────────────────────────────────────────────


def fetch_all_agencies(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Concatena Datafolha, Quaest, Ipespe, Paraná Pesquisas e CNT/MDA.

    Runs all five scrapers in parallel via ThreadPoolExecutor (max_workers=5).
    Deduplicates by (poll_id, candidato, fonte) when poll_id is available,
    falling back to (instituto, candidato, data_pesquisa_fim).
    """
    _SCRAPERS: dict[str, Any] = {
        "datafolha": scrape_datafolha,
        "quaest": scrape_quaest,
        "ipespe": scrape_ipespe,
        "parana_pesquisas": scrape_parana_pesquisas,
        "cnt_mda": scrape_cnt_mda,
    }

    futures_map: dict = {}
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="agency") as executor:
        for source, fn in _SCRAPERS.items():
            futures_map[executor.submit(fn, year, cargo)] = source

    frames: list[pd.DataFrame] = []
    for future, source in futures_map.items():
        try:
            df = future.result()
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning("fetch_all_agencies: %s falhou: %s", source, exc)

    if not frames:
        logger.warning(
            "fetch_all_agencies: nenhuma agência retornou dados para ano=%d cargo=%s", year, cargo
        )
        return _empty_canonical()

    df_combined = pd.concat(frames, ignore_index=True)

    if "poll_id" in df_combined.columns and df_combined["poll_id"].notna().any():
        df_combined = df_combined.drop_duplicates(subset=["poll_id", "candidato", "fonte"])
    elif "data_pesquisa_fim" in df_combined.columns:
        df_combined = df_combined.drop_duplicates(
            subset=["instituto", "candidato", "data_pesquisa_fim"]
        )

    logger.info(
        "fetch_all_agencies: %d linhas combinadas (5 agências) para ano=%d cargo=%s",
        len(df_combined),
        year,
        cargo,
    )
    return df_combined


# ── cross_validate_with_agencies ──────────────────────────────────────────────


def cross_validate_with_agencies(
    df_tse: pd.DataFrame, year: int, cargo: str
) -> pd.DataFrame:
    """Cross-validate TSE poll data against agency sites.

    When TSE PDF extraction matches agency site within 2pp:
    → boost record_confidence_score to 0.95
    When divergence > 5pp:
    → log warning, keep TSE value, set confidence to 0.70

    Matching key: (instituto_normalized, candidato_normalized, date_window).
    Date window: agency date within ±3 days of TSE date (handles publication lag).

    Args:
        df_tse: DataFrame from fetch_pesqele_csv + enrich_with_pdfs.
        year: Election year — passed to agency scrapers.
        cargo: Cargo string ("presidente", "governador", etc.).

    Returns:
        df_tse copy with record_confidence_score updated per match result.
    """
    if df_tse.empty:
        return df_tse

    if "intencao_pct" not in df_tse.columns or "record_confidence_score" not in df_tse.columns:
        logger.warning("cross_validate_with_agencies: colunas obrigatórias ausentes em df_tse")
        return df_tse

    # Fetch agency data in parallel
    df_agencies = _fetch_agencies_parallel(year, cargo)
    if df_agencies.empty:
        logger.info("cross_validate_with_agencies: sem dados de agências — df_tse inalterado")
        return df_tse

    df_out = df_tse.copy()

    # Build agency lookup: (instituto_norm, candidato_norm) → list of (data_fim, intencao_pct)
    agency_lookup: dict[tuple[str, str], list[tuple[Any, float]]] = {}
    for _, row in df_agencies.iterrows():
        if pd.isna(row.get("intencao_pct")):
            continue
        key = (
            _normalize_instituto(row.get("instituto")),
            _normalize_candidato(row.get("candidato")),
        )
        agency_lookup.setdefault(key, []).append(
            (row.get("data_pesquisa_fim"), float(row["intencao_pct"]))
        )

    matched = 0
    boosted = 0
    degraded = 0

    for idx, tse_row in df_out.iterrows():
        if pd.isna(tse_row.get("intencao_pct")):
            continue

        key = (
            _normalize_instituto(tse_row.get("instituto")),
            _normalize_candidato(tse_row.get("candidato")),
        )

        agency_entries = agency_lookup.get(key)
        if not agency_entries:
            continue

        tse_pct = float(tse_row["intencao_pct"])
        tse_date = tse_row.get("data_pesquisa_fim")

        best_diff = _find_best_match(agency_entries, tse_pct, tse_date)
        if best_diff is None:
            continue

        matched += 1
        if best_diff <= _MATCH_THRESHOLD_PP:
            df_out.at[idx, "record_confidence_score"] = _CONFIDENCE_MATCH
            boosted += 1
        elif best_diff > _DIVERGENCE_THRESHOLD_PP:
            logger.warning(
                "cross_validate: divergência %.1fpp para instituto=%s candidato=%s data=%s "
                "(TSE=%.1f%%) — confidence rebaixado para %.2f",
                best_diff,
                tse_row.get("instituto"),
                tse_row.get("candidato"),
                tse_date,
                tse_pct,
                _CONFIDENCE_DIVERGE,
            )
            df_out.at[idx, "record_confidence_score"] = _CONFIDENCE_DIVERGE
            degraded += 1

    logger.info(
        "cross_validate_with_agencies: %d matches — %d boostados (→0.95), %d rebaixados (→0.70)",
        matched,
        boosted,
        degraded,
    )
    return df_out


def _fetch_agencies_parallel(year: int, cargo: str) -> pd.DataFrame:
    """Run all five agency scrapers in parallel; return combined DataFrame."""
    _SCRAPERS: dict[str, Any] = {
        "datafolha": scrape_datafolha,
        "quaest": scrape_quaest,
        "ipespe": scrape_ipespe,
        "parana_pesquisas": scrape_parana_pesquisas,
        "cnt_mda": scrape_cnt_mda,
    }
    results: list[pd.DataFrame] = []

    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="xval") as executor:
        futures = {executor.submit(fn, year, cargo): source for source, fn in _SCRAPERS.items()}
        for future, source in futures.items():
            try:
                df = future.result()
                if not df.empty:
                    results.append(df)
            except Exception as exc:
                logger.warning("_fetch_agencies_parallel: %s falhou: %s", source, exc)

    if not results:
        return _empty_canonical()
    return pd.concat(results, ignore_index=True)


def _normalize_candidato(name: str | None) -> str:
    if not name:
        return ""
    # Normalize to first + last word for fuzzy matching across sources
    name = re.sub(r"\s+", " ", name.strip().lower())
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1]}"
    return name


def _find_best_match(
    agency_entries: list[tuple[Any, float]],
    tse_pct: float,
    tse_date: Any,
) -> float | None:
    """Return the smallest absolute difference found within ±3-day date window.

    If tse_date is NaT/None, falls back to any entry regardless of date.
    Returns None if no entries qualify.
    """
    best: float | None = None

    for agency_date, agency_pct in agency_entries:
        if tse_date is not None and not pd.isna(tse_date):
            if agency_date is not None and not pd.isna(agency_date):
                try:
                    delta = abs((pd.Timestamp(tse_date) - pd.Timestamp(agency_date)).days)
                    if delta > 3:
                        continue
                except Exception:
                    pass  # date parse failure — accept the entry anyway

        diff = abs(tse_pct - agency_pct)
        if best is None or diff < best:
            best = diff

    return best
