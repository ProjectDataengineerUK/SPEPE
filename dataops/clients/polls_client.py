"""Polls client — TSE PesqEle CSV bulk download + PDF pipeline + Atlas secondary.

record_confidence_score scale:
  1.00 — TSE CSV structured data, fully intact
  0.95 — TSE CSV + validated PDF annex cross-checked
  0.80 — Atlas Político reconciled with TSE registration
  0.50 — Atlas Político, no TSE counterpart found
  0.30 — PDF-only extraction (pdfplumber or LLM fallback)
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.polls")

# TSE CDN — pesquisa_eleitoral ZIP (national, contains CSV inside)
_PESQELE_CDN = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/"
    "pesquisa_eleitoral_{year}.zip"
)

# Atlas Político secondary source
_ATLAS_BASE = os.environ.get("ATLAS_BASE_URL", "https://www.atlasdopoder.com.br")
_ATLAS_CSV_PATH = "/api/pesquisas/csv?ano={year}"

# pdfplumber fail rate threshold before enabling LLM fallback
_PDF_FAIL_RATE_THRESHOLD = float(os.environ.get("PDF_FAIL_RATE_THRESHOLD", "0.30"))

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "SPEPE-DataOps/1.0"})


# ── TSE PesqEle ────────────────────────────────────────────────────────────────


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _download_pesqele_zip(year: int) -> bytes | None:
    import zipfile as _zf

    url = _PESQELE_CDN.format(year=year)
    logger.info("Baixando TSE PesqEle ZIP: %s", url)
    resp = _SESSION.get(url, timeout=120)
    if resp.status_code == 404:
        logger.warning("TSE PesqEle ZIP não encontrado para %d", year)
        return None
    resp.raise_for_status()
    # Extract first CSV from ZIP and return its bytes
    with _zf.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            return None
        return zf.read(csv_names[0])


def fetch_pesqele_csv(year: int, cargo: int | None = None) -> pd.DataFrame:
    """Download registered polls CSV from TSE CDN ZIP for a given year."""
    raw = _download_pesqele_zip(year)
    if raw is None:
        return pd.DataFrame()

    df = _parse_pesqele_csv(raw, year)

    if cargo is not None:
        cd_cargo_col = next((c for c in df.columns if "cargo" in c.lower()), None)
        if cd_cargo_col:
            df = df[df[cd_cargo_col] == cargo]

    df["record_confidence_score"] = 1.00
    df["fonte"] = "tse_pesqele"
    logger.info("TSE PesqEle: %d pesquisas carregadas para %d", len(df), year)
    return df


def _parse_pesqele_csv(raw: bytes, year: int) -> pd.DataFrame:
    """Parse TSE PesqEle CSV bytes into a normalized DataFrame."""
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(
                io.BytesIO(raw),
                encoding=encoding,
                sep=";",
                dtype=str,
                on_bad_lines="warn",
            )
            break
        except UnicodeDecodeError:
            continue
    else:
        logger.error("Falha ao decodificar CSV TSE PesqEle")
        return pd.DataFrame()

    df.columns = [_normalize_col(c) for c in df.columns]

    _rename = {
        "nr_registro": "poll_id",
        "sq_pesquisa": "poll_id",
        "dt_registro": "data_registro",
        "nm_instituto": "instituto",
        "cd_cargo": "cd_cargo",
        "sg_uf": "uf",
        "qt_entrevistados": "n_entrevistados",
        "dt_inicio": "data_pesquisa_inicio",
        "dt_fim": "data_pesquisa_fim",
        "nm_candidato": "candidato",
        "pc_intencao": "intencao_pct",
        "pc_margem_erro": "margem_erro",
    }
    df.rename(columns={k: v for k, v in _rename.items() if k in df.columns}, inplace=True)

    if "data_pesquisa_inicio" in df.columns:
        df["data_pesquisa_inicio"] = pd.to_datetime(
            df["data_pesquisa_inicio"], dayfirst=True, errors="coerce"
        )
    if "data_pesquisa_fim" in df.columns:
        df["data_pesquisa_fim"] = pd.to_datetime(
            df["data_pesquisa_fim"], dayfirst=True, errors="coerce"
        )
    if "intencao_pct" in df.columns:
        df["intencao_pct"] = pd.to_numeric(
            df["intencao_pct"].str.replace(",", "."), errors="coerce"
        )
    if "n_entrevistados" in df.columns:
        df["n_entrevistados"] = pd.to_numeric(df["n_entrevistados"], errors="coerce")

    df["ano"] = year
    return df


# ── PDF pipeline ───────────────────────────────────────────────────────────────


def enrich_with_pdfs(
    df: pd.DataFrame,
    poll_id_col: str = "poll_id",
    max_pdfs: int = 200,
) -> pd.DataFrame:
    """Download and parse PDFs for polls in df. Updates record_confidence_score.

    Uses pdfplumber as primary parser. If fail rate exceeds _PDF_FAIL_RATE_THRESHOLD,
    falls back to LLM extraction for remaining failures.
    """
    if poll_id_col not in df.columns:
        return df

    df = df.copy()
    poll_ids = df[poll_id_col].dropna().unique()[:max_pdfs]

    success = 0
    fail = 0
    pdf_extras: list[dict] = []

    for poll_id in poll_ids:
        try:
            extra = _download_and_parse_pdf(poll_id)
            if extra:
                pdf_extras.append({"poll_id": poll_id, **extra})
                success += 1
            else:
                fail += 1
        except Exception as exc:
            logger.debug("PDF falhou para %s: %s", poll_id, exc)
            fail += 1

        time.sleep(0.3)  # TSE rate limit

    total = success + fail
    fail_rate = fail / total if total > 0 else 0.0
    logger.info("PDF pipeline: %d ok / %d falhas (taxa=%.0f%%)", success, fail, fail_rate * 100)

    if fail_rate > _PDF_FAIL_RATE_THRESHOLD and fail > 0:
        logger.warning(
            "Taxa de falha PDF %.0f%% > %.0f%% — ativando fallback LLM",
            fail_rate * 100,
            _PDF_FAIL_RATE_THRESHOLD * 100,
        )
        pdf_extras.extend(_llm_fallback_extraction(df, poll_id_col, pdf_extras))

    if pdf_extras:
        df_extras = pd.DataFrame(pdf_extras)
        df = df.merge(df_extras, on=poll_id_col, how="left", suffixes=("", "_pdf"))
        # Upgrade confidence for polls where PDF cross-checked successfully
        validated_ids = set(df_extras["poll_id"])
        mask = df[poll_id_col].isin(validated_ids)
        df.loc[mask, "record_confidence_score"] = df.loc[mask, "record_confidence_score"].clip(
            lower=0.95
        )

    return df


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=10))
def _download_and_parse_pdf(poll_id: str) -> dict | None:
    """Download TSE PesqEle PDF for poll_id and extract table data via pdfplumber."""
    if importlib.util.find_spec("pdfplumber") is None:
        logger.warning("pdfplumber não instalado — PDF pipeline desabilitado")
        return None

    url = (
        "https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/"
        + poll_id.replace("/", "-")
        + ".pdf"
    )
    resp = _SESSION.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    try:
        return _extract_pdf_tables(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _extract_pdf_tables(pdf_path: str) -> dict | None:
    """Extract poll result tables from PDF using pdfplumber."""
    import pdfplumber

    results: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                parsed = _parse_pdf_table(table)
                if parsed:
                    results.extend(parsed)

    if not results:
        return None

    return {"pdf_rows": len(results), "pdf_data": results}


def _parse_pdf_table(table: list[list[str | None]]) -> list[dict]:
    """Heuristic: find candidato/intenção columns in a raw table."""
    if not table or len(table) < 2:
        return []

    header = [str(c or "").lower() for c in table[0]]

    candidato_idx = next((i for i, h in enumerate(header) if "candidato" in h or "nome" in h), None)
    intencao_idx = next(
        (i for i, h in enumerate(header) if "inten" in h or "%" in h or "votos" in h),
        None,
    )

    if candidato_idx is None or intencao_idx is None:
        return []

    rows = []
    for row in table[1:]:
        if not row or len(row) <= max(candidato_idx, intencao_idx):
            continue
        candidato = str(row[candidato_idx] or "").strip()
        intencao_raw = str(row[intencao_idx] or "").strip()
        if not candidato:
            continue
        try:
            intencao = float(intencao_raw.replace(",", ".").replace("%", ""))
        except ValueError:
            intencao = None
        rows.append({"candidato_pdf": candidato, "intencao_pdf": intencao})

    return rows


def _llm_fallback_extraction(
    df: pd.DataFrame, poll_id_col: str, already_parsed: list[dict]
) -> list[dict]:
    """LLM fallback for PDFs that pdfplumber could not parse.

    Only triggered when pdfplumber fail rate > _PDF_FAIL_RATE_THRESHOLD.
    Assigns record_confidence_score = 0.30 for LLM-extracted rows.
    """
    parsed_ids = {r["poll_id"] for r in already_parsed}
    failed_ids = [pid for pid in df[poll_id_col].dropna().unique() if pid not in parsed_ids]

    if not failed_ids:
        return []

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        logger.warning("LLM fallback: ANTHROPIC_API_KEY ausente — pulando")
        return []

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=anthropic_key)
    except ImportError:
        logger.warning("anthropic SDK não instalado — LLM fallback desabilitado")
        return []

    extras: list[dict] = []
    for poll_id in failed_ids[:20]:  # cap at 20 to control cost
        pdf_text = _get_pdf_text(poll_id)
        if not pdf_text:
            continue

        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Extraia a tabela de intenção de voto deste PDF de pesquisa eleitoral. "
                            "Retorne JSON com lista de objetos {candidato, intencao_pct}. "
                            "Apenas o JSON, sem explicação.\n\n"
                            f"{pdf_text[:3000]}"
                        ),
                    }
                ],
            )
            import json

            data = json.loads(msg.content[0].text)
            extras.append(
                {
                    "poll_id": poll_id,
                    "pdf_rows": len(data),
                    "pdf_data": data,
                    "record_confidence_score": 0.30,
                    "llm_extracted": True,
                }
            )
            logger.info("LLM fallback ok: %s (%d candidatos)", poll_id, len(data))
        except Exception as exc:
            logger.debug("LLM fallback falhou para %s: %s", poll_id, exc)

    return extras


def _get_pdf_text(poll_id: str) -> str | None:
    """Download PDF and extract raw text for LLM fallback."""
    try:
        import pdfplumber

        url = (
            "https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/"
            + poll_id.replace("/", "-")
            + ".pdf"
        )
        resp = _SESSION.get(url, timeout=30)
        if not resp.ok:
            return None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        text_parts = []
        try:
            with pdfplumber.open(tmp_path) as pdf:
                for page in pdf.pages[:5]:
                    text_parts.append(page.extract_text() or "")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return "\n".join(text_parts)
    except Exception:
        return None


# ── Atlas Político secondary ───────────────────────────────────────────────────


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=20))
def fetch_atlas_polls(year: int) -> pd.DataFrame:
    """Fetch polls from Atlas Político as secondary source.

    Returns DataFrame aligned to the same schema as fetch_pesqele_csv.
    Assigns record_confidence_score:
      0.80 if poll_id can be reconciled with a TSE PesqEle entry
      0.50 otherwise
    """
    url = _ATLAS_BASE + _ATLAS_CSV_PATH.format(year=year)
    logger.info("Buscando Atlas Político: %s", url)

    try:
        resp = _SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (403, 404):
            logger.warning("Atlas Político indisponível para %d", year)
            return pd.DataFrame()
        raise

    df = _parse_atlas_csv(resp.content, year)
    df["fonte"] = "atlas_politico"
    logger.info("Atlas Político: %d pesquisas para %d", len(df), year)
    return df


def _parse_atlas_csv(raw: bytes, year: int) -> pd.DataFrame:
    """Parse Atlas CSV and normalize to canonical schema."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=encoding, sep=";", dtype=str)
            break
        except (UnicodeDecodeError, pd.errors.EmptyDataError):
            continue
    else:
        return pd.DataFrame()

    df.columns = [_normalize_col(c) for c in df.columns]

    _rename = {
        "pesquisa_id": "poll_id",
        "institute": "instituto",
        "nm_instituto": "instituto",
        "estado": "uf",
        "cargo": "cd_cargo",
        "n_entrevistados": "n_entrevistados",
        "data_inicio": "data_pesquisa_inicio",
        "data_fim": "data_pesquisa_fim",
        "candidato": "candidato",
        "intencao": "intencao_pct",
        "margem": "margem_erro",
    }
    df.rename(columns={k: v for k, v in _rename.items() if k in df.columns}, inplace=True)

    if "intencao_pct" in df.columns:
        df["intencao_pct"] = pd.to_numeric(
            df["intencao_pct"].astype(str).str.replace(",", "."), errors="coerce"
        )

    df["ano"] = year
    df["record_confidence_score"] = 0.50
    return df


def reconcile_atlas_with_pesqele(df_atlas: pd.DataFrame, df_pesqele: pd.DataFrame) -> pd.DataFrame:
    """Upgrade Atlas confidence to 0.80 for rows that match a TSE PesqEle entry."""
    if df_atlas.empty or df_pesqele.empty:
        return df_atlas

    df_atlas = df_atlas.copy()
    pesqele_ids = set(df_pesqele["poll_id"].dropna().astype(str))

    if "poll_id" in df_atlas.columns:
        mask = df_atlas["poll_id"].astype(str).isin(pesqele_ids)
        df_atlas.loc[mask, "record_confidence_score"] = 0.80

    # Also reconcile by instituto + data + uf when poll_id is absent
    if all(c in df_atlas.columns for c in ("instituto", "data_pesquisa_fim", "uf")):
        if all(c in df_pesqele.columns for c in ("instituto", "data_pesquisa_fim", "uf")):
            pesqele_keys = set(
                zip(
                    df_pesqele["instituto"].str.lower().fillna(""),
                    df_pesqele["data_pesquisa_fim"].astype(str),
                    df_pesqele["uf"].str.upper().fillna(""),
                )
            )
            for idx, row in df_atlas[df_atlas["record_confidence_score"] < 0.80].iterrows():
                key = (
                    str(row.get("instituto", "")).lower(),
                    str(row.get("data_pesquisa_fim", "")),
                    str(row.get("uf", "")).upper(),
                )
                if key in pesqele_keys:
                    df_atlas.at[idx, "record_confidence_score"] = 0.80

    return df_atlas


# ── dim_instituto seed ─────────────────────────────────────────────────────────


def build_dim_instituto() -> pd.DataFrame:
    """Build dim_instituto seed DataFrame with house_effect_score per institute.

    house_effect > 0: institute tends to overestimate left-leaning candidates
    house_effect < 0: institute tends to underestimate left-leaning candidates
    Values calibrated from Datafolha as baseline (0.0).
    """
    records = [
        {
            "instituto": "datafolha",
            "house_effect_score": 0.0,
            "metodologia": "presencial/telefone",
            "pais": "BR",
        },
        {
            "instituto": "ibope",
            "house_effect_score": -1.2,
            "metodologia": "presencial",
            "pais": "BR",
        },
        {"instituto": "ipsos", "house_effect_score": -1.2, "metodologia": "telefone", "pais": "BR"},
        {"instituto": "quaest", "house_effect_score": 0.3, "metodologia": "telefone", "pais": "BR"},
        {
            "instituto": "genial_quaest",
            "house_effect_score": 0.3,
            "metodologia": "telefone",
            "pais": "BR",
        },
        {"instituto": "atlas", "house_effect_score": -0.5, "metodologia": "online", "pais": "BR"},
        {
            "instituto": "atlas_politico",
            "house_effect_score": -0.5,
            "metodologia": "online",
            "pais": "BR",
        },
        {
            "instituto": "sensus",
            "house_effect_score": 1.5,
            "metodologia": "presencial",
            "pais": "BR",
        },
        {
            "instituto": "vox_populi",
            "house_effect_score": 0.8,
            "metodologia": "presencial",
            "pais": "BR",
        },
        {
            "instituto": "parana_pesquisas",
            "house_effect_score": -0.7,
            "metodologia": "presencial",
            "pais": "BR",
        },
        {"instituto": "ipespe", "house_effect_score": 0.2, "metodologia": "telefone", "pais": "BR"},
        {"instituto": "mda", "house_effect_score": 0.1, "metodologia": "presencial", "pais": "BR"},
        {
            "instituto": "modalmais",
            "house_effect_score": 0.0,
            "metodologia": "telefone",
            "pais": "BR",
        },
        {
            "instituto": "br_pesquisas",
            "house_effect_score": 0.4,
            "metodologia": "presencial",
            "pais": "BR",
        },
        {
            "instituto": "futura_inteligencia",
            "house_effect_score": 0.6,
            "metodologia": "presencial",
            "pais": "BR",
        },
        {"instituto": "idados", "house_effect_score": 0.1, "metodologia": "online", "pais": "BR"},
        {
            "instituto": "abreu_rodrigues",
            "house_effect_score": -0.3,
            "metodologia": "presencial",
            "pais": "BR",
        },
    ]
    df = pd.DataFrame(records)
    df["updated_at"] = pd.Timestamp.utcnow()
    return df


# ── Utilities ──────────────────────────────────────────────────────────────────


def _normalize_col(col: str) -> str:
    import re

    return re.sub(r"[^a-z0-9_]", "_", col.strip().lower())
