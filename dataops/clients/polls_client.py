"""Polls client — TSE PesqEle CSV bulk download + PDF pipeline + Atlas secondary.

record_confidence_score scale:
  1.00 — TSE CSV with nm_candidato/pc_intencao direct (legacy pre-2024 format)
  0.95 — TSE CSV + PDF cross-validated
  0.85 — Gemini Flash multimodal extracted successfully
  0.70 — pdfplumber extracted successfully
  0.30 — PDF downloaded but extraction failed (metadata preserved)
  0.10 — PDF unavailable or unreadable
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from pydantic import BaseModel, field_validator, model_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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
    url = _PESQELE_CDN.format(year=year)
    logger.info("Baixando TSE PesqEle ZIP: %s", url)
    resp = _SESSION.get(url, timeout=120)
    if resp.status_code == 404:
        logger.warning("TSE PesqEle ZIP não encontrado para %d", year)
        return None
    resp.raise_for_status()
    return resp.content  # full ZIP bytes; caller extracts all per-UF CSVs


def fetch_pesqele_csv(year: int, cargo: int | str | None = None) -> pd.DataFrame:
    """Download registered polls from TSE CDN ZIP for a given year.

    The ZIP contains one CSV per UF (e.g. pesquisa_eleitoral_2026_SP.csv).
    All CSVs are read and concatenated.
    cargo: optional filter — int matches cd_cargo, str matches ds_cargo text.
    """
    import zipfile as _zf

    raw_zip = _download_pesqele_zip(year)
    if raw_zip is None:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    with _zf.ZipFile(io.BytesIO(raw_zip)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            logger.warning("TSE PesqEle ZIP vazio para %d", year)
            return pd.DataFrame()
        for csv_name in csv_names:
            raw = zf.read(csv_name)
            df_part = _parse_pesqele_csv(raw, year)
            if not df_part.empty:
                frames.append(df_part)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    if cargo is not None:
        if isinstance(cargo, int) and "cd_cargo" in df.columns:
            df = df[df["cd_cargo"] == str(cargo)]
        elif isinstance(cargo, str) and "ds_cargo" in df.columns:
            df = df[df["ds_cargo"].str.upper() == cargo.upper()]

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
        # TSE CDN 2024+ column names
        "nr_protocolo_registro": "poll_id",
        "nm_empresa_fantasia": "instituto",  # nm_empresa kept as-is to avoid dup
        "qt_entrevistado": "n_entrevistados",
        "dt_inicio_pesquisa": "data_pesquisa_inicio",
        "dt_fim_pesquisa": "data_pesquisa_fim",
        "sg_uf": "uf",
        # Legacy column names (pre-2024)
        "nr_registro": "poll_id",
        "sq_pesquisa": "poll_id",
        "dt_registro": "data_registro",
        "nm_instituto": "instituto",
        "qt_entrevistados": "n_entrevistados",
        "dt_inicio": "data_pesquisa_inicio",
        "dt_fim": "data_pesquisa_fim",
        "nm_candidato": "candidato",
        "pc_intencao": "intencao_pct",
        "pc_margem_erro": "margem_erro",
    }
    df.rename(columns={k: v for k, v in _rename.items() if k in df.columns}, inplace=True)

    if "data_pesquisa_inicio" in df.columns:
        df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    if "data_pesquisa_fim" in df.columns:
        df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    if "intencao_pct" in df.columns:
        df["intencao_pct"] = pd.to_numeric(
            df["intencao_pct"].str.replace(",", "."), errors="coerce"
        )
    if "n_entrevistados" in df.columns:
        df["n_entrevistados"] = pd.to_numeric(df["n_entrevistados"], errors="coerce")

    df["ano"] = year
    return df


# ── PDF pipeline ───────────────────────────────────────────────────────────────

_PDF_BASE_URL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/{poll_id_safe}.pdf"
)

_VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "")

_BATCH_SIZE = 10  # PDFs per Gemini batch — balances cost vs. latency


# ── Pydantic models ────────────────────────────────────────────────────────────


class PollResult(BaseModel):
    candidato: str
    intencao_pct: float
    rejeicao_pct: float | None = None
    tipo_pesquisa: str  # "espontanea" | "estimulada" | "unknown"
    confidence: float = 0.85

    @field_validator("intencao_pct")
    @classmethod
    def validate_intencao_range(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"intencao_pct={v} fora do intervalo [0, 100]")
        return round(v, 2)

    @field_validator("rejeicao_pct")
    @classmethod
    def validate_rejeicao_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 100.0):
            raise ValueError(f"rejeicao_pct={v} fora do intervalo [0, 100]")
        return round(v, 2) if v is not None else None

    @field_validator("tipo_pesquisa")
    @classmethod
    def normalize_tipo(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("espontanea", "estimulada", "unknown"):
            return "unknown"
        return v


class _PollRound(BaseModel):
    """Validates that a full round of candidates sums to approximately 100%."""

    results: list[PollResult]

    @model_validator(mode="after")
    def validate_round_sum(self) -> "_PollRound":
        for tipo in ("espontanea", "estimulada"):
            subset = [r for r in self.results if r.tipo_pesquisa == tipo]
            if not subset:
                continue
            total = sum(r.intencao_pct for r in subset)
            if not (85.0 <= total <= 115.0):
                logger.warning(
                    "Soma de intencao_pct para tipo=%s = %.1f%% (esperado ~100%%)",
                    tipo,
                    total,
                )
        return self


# ── Gemini Flash prompt ────────────────────────────────────────────────────────

_GEMINI_SYSTEM_PROMPT = """\
Você é um analista eleitoral especializado em extrair dados estruturados de \
PDFs de pesquisas eleitorais registradas no TSE PesqEle. \
Sua tarefa é localizar todas as tabelas de intenção de voto e retornar \
exatamente o que está escrito no documento — sem inferência, sem valores inventados.\
"""

_GEMINI_EXTRACTION_PROMPT = """\
Analise este PDF de pesquisa eleitoral TSE PesqEle e extraia TODAS as tabelas \
de intenção de voto.

REGRAS:
- Extraia separadamente pesquisa ESPONTÂNEA e ESTIMULADA quando ambas existirem.
- Para cada candidato em cada rodada, retorne um objeto JSON.
- Se rejeição não constar na tabela, use null.
- Valores percentuais: número decimal sem símbolo "%" (ex: 34.5, não "34,5%").
- Se uma tabela for ilegível ou ausente, retorne lista vazia [].
- Retorne APENAS o JSON array, sem markdown, sem explicação.

OUTPUT FORMAT:
[
  {"candidato": str, "intencao_pct": float, "rejeicao_pct": float|null, "tipo": "espontanea"|"estimulada"}
]

EXEMPLOS:

Exemplo 1 — PDF com pesquisa estimulada clara:
Tabela encontrada: Candidato A 38,5%, Candidato B 22,0%, Outros 12%, Brancos/Nulos 8%
Output esperado:
[
  {"candidato": "Candidato A", "intencao_pct": 38.5, "rejeicao_pct": null, "tipo": "estimulada"},
  {"candidato": "Candidato B", "intencao_pct": 22.0, "rejeicao_pct": null, "tipo": "estimulada"},
  {"candidato": "Outros", "intencao_pct": 12.0, "rejeicao_pct": null, "tipo": "estimulada"},
  {"candidato": "Brancos/Nulos", "intencao_pct": 8.0, "rejeicao_pct": null, "tipo": "estimulada"}
]

Exemplo 2 — PDF com espontânea + estimulada e coluna de rejeição:
Tabela espontânea: X 15%, Y 9%, NS/NR 76%
Tabela estimulada com rejeição: X 41% (rejeição 28%), Y 35% (rejeição 19%), NS/NR 24%
Output esperado:
[
  {"candidato": "X", "intencao_pct": 15.0, "rejeicao_pct": null, "tipo": "espontanea"},
  {"candidato": "Y", "intencao_pct": 9.0, "rejeicao_pct": null, "tipo": "espontanea"},
  {"candidato": "NS/NR", "intencao_pct": 76.0, "rejeicao_pct": null, "tipo": "espontanea"},
  {"candidato": "X", "intencao_pct": 41.0, "rejeicao_pct": 28.0, "tipo": "estimulada"},
  {"candidato": "Y", "intencao_pct": 35.0, "rejeicao_pct": 19.0, "tipo": "estimulada"},
  {"candidato": "NS/NR", "intencao_pct": 24.0, "rejeicao_pct": null, "tipo": "estimulada"}
]

Agora extraia do PDF anexo:\
"""


# ── Gemini Flash extractor ─────────────────────────────────────────────────────


def _build_gemini_client() -> Any:
    """Lazy-init Vertex AI generative model. Raises ImportError if SDK absent."""
    import vertexai
    from vertexai.generative_models import GenerativeModel

    vertexai.init(project=_GCP_PROJECT, location=_VERTEX_LOCATION)
    return GenerativeModel(
        "gemini-2.0-flash-001",
        system_instruction=_GEMINI_SYSTEM_PROMPT,
    )


# Retry specifically on quota and transient errors; let InvalidArgument propagate
# after 2 attempts so the caller falls through to failure recording.
@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _call_gemini_with_retry(model: Any, parts: list) -> str:
    from google.api_core.exceptions import ResourceExhausted

    try:
        response = model.generate_content(
            parts,
            generation_config={
                "max_output_tokens": 1024,
                "temperature": 0.0,
                "response_mime_type": "application/json",
            },
        )
        return response.text
    except ResourceExhausted:
        logger.warning("Gemini quota esgotada — aguardando retry exponencial")
        raise


def _extract_with_gemini_flash(pdf_bytes: bytes, poll_id: str) -> list[PollResult]:
    """Send PDF bytes to Gemini Flash and return validated PollResult list.

    Uses PDF native support (Part.from_data with application/pdf) so the model
    sees the rendered pages, not extracted text — handles image-based PDFs.
    """
    try:
        from vertexai.generative_models import Part
    except ImportError:
        logger.warning("vertexai SDK ausente — Gemini Flash desabilitado")
        return []

    try:
        model = _build_gemini_client()
    except Exception as exc:
        logger.warning("Falha ao inicializar Gemini: %s", exc)
        return []

    pdf_part = Part.from_data(pdf_bytes, mime_type="application/pdf")
    text_part = _GEMINI_EXTRACTION_PROMPT

    try:
        raw_json = _call_gemini_with_retry(model, [pdf_part, text_part])
    except Exception as exc:
        logger.debug("Gemini Flash falhou para poll_id=%s: %s", poll_id, exc)
        return []

    try:
        data = json.loads(raw_json)
        if not isinstance(data, list):
            logger.warning("Gemini retornou não-lista para %s — descartando", poll_id)
            return []
    except json.JSONDecodeError as exc:
        logger.warning("JSON inválido do Gemini para %s: %s", poll_id, exc)
        return []

    validated: list[PollResult] = []
    for item in data:
        try:
            validated.append(
                PollResult(
                    candidato=str(item.get("candidato", "")),
                    intencao_pct=float(item.get("intencao_pct", 0)),
                    rejeicao_pct=item.get("rejeicao_pct"),
                    tipo_pesquisa=str(item.get("tipo", "unknown")),
                    confidence=0.85,
                )
            )
        except Exception as exc:
            logger.debug("PollResult inválido descartado (%s): %s", poll_id, exc)

    if validated:
        _PollRound(results=validated)  # triggers round-sum warning if needed

    return validated


# ── pdfplumber extractor ───────────────────────────────────────────────────────


def _extract_with_pdfplumber(pdf_bytes: bytes) -> list[dict]:
    """Fast path: structured-text PDFs only. Returns raw row dicts."""
    try:
        import pdfplumber
    except ImportError:
        return []

    results: list[dict] = []
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    results.extend(_parse_pdf_table(table))
    except Exception as exc:
        logger.debug("pdfplumber error: %s", exc)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return results


def _parse_pdf_table(table: list[list[str | None]]) -> list[dict]:
    """Heuristic column detection for candidato/intenção in raw PDF tables."""
    if not table or len(table) < 2:
        return []

    header = [str(c or "").lower() for c in table[0]]
    candidato_idx = next(
        (i for i, h in enumerate(header) if "candidato" in h or "nome" in h), None
    )
    intencao_idx = next(
        (i for i, h in enumerate(header) if "inten" in h or "%" in h or "votos" in h), None
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


# ── PDF download ───────────────────────────────────────────────────────────────


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=10))
def _download_pdf(poll_id: str) -> bytes | None:
    """Return raw PDF bytes or None on 404/error."""
    url = _PDF_BASE_URL.format(poll_id_safe=poll_id.replace("/", "-"))
    try:
        resp = _SESSION.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        logger.debug("Falha ao baixar PDF %s: %s", poll_id, exc)
        raise


def enrich_with_pdfs(
    df: pd.DataFrame,
    poll_id_col: str = "poll_id",
    max_pdfs: int = 200,
) -> pd.DataFrame:
    """Enrich df with voting-intention data extracted from TSE PesqEle PDFs.

    Fallback chain per PDF:
      1. pdfplumber   — fast, zero-cost; works on text-layer PDFs
      2. Gemini Flash — multimodal; works on image-based or complex-layout PDFs
      3. Failure row  — intencao_pct=NaN, confidence=0.10

    confidence is written to record_confidence_score:
      pdfplumber success → 0.70
      Gemini success     → 0.85
      failure            → 0.10

    PDFs are processed in batches of _BATCH_SIZE to control Gemini cost.
    A 0.3 s sleep between downloads respects TSE rate limits.
    """
    if poll_id_col not in df.columns:
        return df

    df = df.copy()
    poll_ids: list[str] = list(df[poll_id_col].dropna().unique()[:max_pdfs])

    pdf_extras: list[dict] = []
    gemini_needed: list[tuple[str, bytes]] = []  # (poll_id, pdf_bytes)

    # ── Pass 1: download + pdfplumber ─────────────────────────────────────────
    for poll_id in poll_ids:
        pdf_bytes = None
        try:
            pdf_bytes = _download_pdf(poll_id)
        except Exception as exc:
            logger.debug("Download falhou para %s: %s", poll_id, exc)

        if pdf_bytes is None:
            pdf_extras.append(
                {
                    poll_id_col: poll_id,
                    "intencao_pct": float("nan"),
                    "record_confidence_score": 0.10,
                    "pdf_source": "unavailable",
                }
            )
            time.sleep(0.3)
            continue

        plumber_rows = _extract_with_pdfplumber(pdf_bytes)
        if plumber_rows:
            pdf_extras.append(
                {
                    poll_id_col: poll_id,
                    "pdf_rows": len(plumber_rows),
                    "pdf_data": plumber_rows,
                    "record_confidence_score": 0.70,
                    "pdf_source": "pdfplumber",
                }
            )
        else:
            gemini_needed.append((poll_id, pdf_bytes))

        time.sleep(0.3)

    # ── Pass 2: Gemini Flash in batches ───────────────────────────────────────
    for batch_start in range(0, len(gemini_needed), _BATCH_SIZE):
        batch = gemini_needed[batch_start : batch_start + _BATCH_SIZE]
        for poll_id, pdf_bytes in batch:
            gemini_rows = _extract_with_gemini_flash(pdf_bytes, poll_id)
            if gemini_rows:
                pdf_extras.append(
                    {
                        poll_id_col: poll_id,
                        "pdf_rows": len(gemini_rows),
                        "pdf_data": [r.model_dump() for r in gemini_rows],
                        "record_confidence_score": 0.85,
                        "pdf_source": "gemini_flash",
                    }
                )
            else:
                pdf_extras.append(
                    {
                        poll_id_col: poll_id,
                        "intencao_pct": float("nan"),
                        "record_confidence_score": 0.10,
                        "pdf_source": "failed",
                    }
                )

    # ── Merge results into df ─────────────────────────────────────────────────
    if not pdf_extras:
        return df

    df_extras = pd.DataFrame(pdf_extras)
    df = df.merge(df_extras, on=poll_id_col, how="left", suffixes=("", "_pdf"))

    for source, score in (("pdfplumber", 0.70), ("gemini_flash", 0.85)):
        mask = df.get("pdf_source", pd.Series()) == source
        if mask.any():
            current = df.loc[mask, "record_confidence_score"].fillna(0)
            df.loc[mask, "record_confidence_score"] = current.clip(lower=score)

    _log_pipeline_summary(df_extras, poll_id_col)
    return df


def _log_pipeline_summary(df_extras: pd.DataFrame, poll_id_col: str) -> None:
    total = len(df_extras)
    if total == 0:
        return
    counts = df_extras.get("pdf_source", pd.Series(dtype=str)).value_counts().to_dict()
    logger.info(
        "PDF pipeline summary (%d PDFs): pdfplumber=%d, gemini=%d, failed=%d, unavailable=%d",
        total,
        counts.get("pdfplumber", 0),
        counts.get("gemini_flash", 0),
        counts.get("failed", 0),
        counts.get("unavailable", 0),
    )


# ── Atlas Político secondary ───────────────────────────────────────────────────

_ATLAS_SCRAPE_URL = "https://www.atlasintelligencia.com.br/pesquisas"


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
)
def fetch_atlas_politico(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape voting intention polls from Atlas Político public website.

    Returns DataFrame with canonical schema aligned to fetch_pesqele_csv output.
    record_confidence_score = 0.75 (scraping, no API contract).

    Strategy 1 (preferred): parse <script id="__NEXT_DATA__"> Next.js JSON.
    Strategy 2 (fallback):  parse <script> tags containing window.__INITIAL_STATE__.
    Strategy 3 (last resort): parse HTML tables with BeautifulSoup.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — fetch_atlas_politico desabilitado")
        return pd.DataFrame()

    _CARGO_SLUG: dict[str, str] = {
        "presidente": "presidente",
        "governador": "governador",
        "senador": "senador",
    }
    slug = _CARGO_SLUG.get(cargo.lower(), "presidente")
    url = f"{_ATLAS_SCRAPE_URL}?cargo={slug}&ano={year}"

    resp = _SESSION.get(url, timeout=30, headers={"Accept-Language": "pt-BR"})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = (
        _atlas_parse_next_data(soup, year, cargo)
        or _atlas_parse_initial_state(soup, year, cargo)
        or _atlas_parse_html_tables(soup, year, cargo)
    )

    if not rows:
        logger.warning(
            "fetch_atlas_politico: scraping retornou DataFrame vazio para ano=%d cargo=%s",
            year,
            cargo,
        )
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df["intencao_pct"] = pd.to_numeric(df["intencao_pct"], errors="coerce")
    df["n_entrevistados"] = pd.to_numeric(df.get("n_entrevistados", pd.Series(dtype=str)), errors="coerce") if "n_entrevistados" in df.columns else float("nan")
    df["record_confidence_score"] = 0.75
    df["ano"] = year

    if "data_pesquisa_fim" in df.columns:
        df = df[df["data_pesquisa_fim"].dt.year == year]

    logger.info(
        "Atlas Político scrape: %d linhas para ano=%d cargo=%s", len(df), year, cargo
    )
    return df


def _atlas_build_base(pesquisa: dict, year: int, cargo: str) -> dict:
    return {
        "poll_id": pesquisa.get("id") or pesquisa.get("poll_id") or pesquisa.get("registro"),
        "instituto": pesquisa.get("instituto") or pesquisa.get("institute") or pesquisa.get("empresa"),
        "data_pesquisa_inicio": pesquisa.get("data_inicio") or pesquisa.get("dataInicio") or pesquisa.get("data_inicio_pesquisa"),
        "data_pesquisa_fim": pesquisa.get("data_fim") or pesquisa.get("dataFim") or pesquisa.get("data_fim_pesquisa"),
        "n_entrevistados": pesquisa.get("n_entrevistados") or pesquisa.get("entrevistados") or pesquisa.get("amostra"),
        "margem_erro": pesquisa.get("margem_erro") or pesquisa.get("margemErro"),
        "uf": pesquisa.get("uf", "BR"),
        "cd_cargo": cargo,
        "ano": year,
        "fonte": "atlas_politico_scrape",
    }


def _atlas_expand_candidatos(pesquisa: dict, base: dict) -> list[dict]:
    candidatos = (
        pesquisa.get("candidatos")
        or pesquisa.get("resultados")
        or pesquisa.get("candidates")
        or []
    )
    rows = []
    for c in candidatos:
        rows.append({
            **base,
            "candidato": c.get("nome") or c.get("candidato") or c.get("name"),
            "intencao_pct": c.get("intencao_pct") or c.get("intencao") or c.get("percentual") or c.get("value"),
        })
    return rows


def _atlas_parse_next_data(soup: Any, year: int, cargo: str) -> list[dict]:
    """Strategy 1: __NEXT_DATA__ script tag (Next.js standard)."""
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return []
    try:
        next_data = json.loads(tag.string)
    except (json.JSONDecodeError, AttributeError):
        return []

    page_props = next_data.get("props", {}).get("pageProps", {})
    polls_raw = (
        page_props.get("polls")
        or page_props.get("pesquisas")
        or page_props.get("data", {}).get("pesquisas", [])
        or page_props.get("results", [])
    )
    if not polls_raw or not isinstance(polls_raw, list):
        return []

    rows: list[dict] = []
    for pesquisa in polls_raw:
        base = _atlas_build_base(pesquisa, year, cargo)
        expanded = _atlas_expand_candidatos(pesquisa, base)
        if expanded:
            rows.extend(expanded)
        else:
            rows.append({**base, "candidato": None, "intencao_pct": None})
    return rows


def _atlas_parse_initial_state(soup: Any, year: int, cargo: str) -> list[dict]:
    """Strategy 2: window.__INITIAL_STATE__ or similar inline JSON blobs."""
    import re

    _PATTERNS = [
        re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", re.DOTALL),
        re.compile(r"window\.__DATA__\s*=\s*(\{.*?\});", re.DOTALL),
        re.compile(r"window\.__APP_STATE__\s*=\s*(\{.*?\});", re.DOTALL),
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
            polls_raw = (
                blob.get("pesquisas")
                or blob.get("polls")
                or blob.get("data", {}).get("pesquisas", [])
            )
            if not polls_raw or not isinstance(polls_raw, list):
                continue
            rows: list[dict] = []
            for pesquisa in polls_raw:
                base = _atlas_build_base(pesquisa, year, cargo)
                expanded = _atlas_expand_candidatos(pesquisa, base)
                rows.extend(expanded if expanded else [{**base, "candidato": None, "intencao_pct": None}])
            if rows:
                return rows
    return []


def _atlas_parse_html_tables(soup: Any, year: int, cargo: str) -> list[dict]:
    """Strategy 3: raw HTML table parsing via BeautifulSoup."""
    rows: list[dict] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        candidato_idx = next(
            (i for i, h in enumerate(headers) if "candidato" in h or "nome" in h), None
        )
        intencao_idx = next(
            (i for i, h in enumerate(headers) if "inten" in h or "%" in h), None
        )
        if candidato_idx is None or intencao_idx is None:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= max(candidato_idx, intencao_idx):
                continue
            candidato = cells[candidato_idx]
            intencao_raw = cells[intencao_idx].replace(",", ".").replace("%", "").strip()
            if not candidato:
                continue
            try:
                intencao = float(intencao_raw)
            except ValueError:
                intencao = None
            rows.append({
                "poll_id": None,
                "instituto": None,
                "data_pesquisa_inicio": None,
                "data_pesquisa_fim": None,
                "n_entrevistados": None,
                "margem_erro": None,
                "uf": "BR",
                "cd_cargo": cargo,
                "ano": year,
                "fonte": "atlas_politico_scrape",
                "candidato": candidato,
                "intencao_pct": intencao,
            })
    return rows


def scrape_poder360(year: int, cargo: str = "presidente") -> pd.DataFrame:
    """Scrape Poder360 poll aggregator (Next.js __NEXT_DATA__ JSON).

    Returns DataFrame with canonical schema. record_confidence_score = 0.70
    (public aggregator, no API contract). Raises on HTTP error; returns empty
    DataFrame on parse failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("beautifulsoup4 não instalado — scrape_poder360 desabilitado")
        return pd.DataFrame()

    _PODER360_URL = os.environ.get(
        "PODER360_URL", "https://poder360.com.br/agregador-de-pesquisas/"
    )
    _CARGO_SLUG: dict[str, str] = {
        "presidente": "presidente",
        "governador": "governador",
        "senador": "senador",
    }
    slug = _CARGO_SLUG.get(cargo.lower(), "presidente")
    url = f"{_PODER360_URL}?cargo={slug}&ano={year}"

    resp = _SESSION.get(url, timeout=30, headers={"Accept-Language": "pt-BR"})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        logger.warning("Poder360: __NEXT_DATA__ não encontrado — estrutura mudou")
        return pd.DataFrame()

    try:
        next_data = json.loads(script_tag.string)
        page_props = next_data.get("props", {}).get("pageProps", {})
        polls_raw = (
            page_props.get("polls")
            or page_props.get("pesquisas")
            or page_props.get("data", {}).get("pesquisas", [])
        )
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("Poder360: falha parse JSON: %s", exc)
        return pd.DataFrame()

    if not polls_raw:
        logger.warning("Poder360: array de pesquisas vazio")
        return pd.DataFrame()

    rows = []
    for pesquisa in polls_raw:
        base = {
            "poll_id": pesquisa.get("id") or pesquisa.get("registro"),
            "instituto": pesquisa.get("instituto") or pesquisa.get("institute"),
            "data_pesquisa_inicio": pesquisa.get("data_inicio") or pesquisa.get("dataInicio"),
            "data_pesquisa_fim": pesquisa.get("data_fim") or pesquisa.get("dataFim"),
            "n_entrevistados": pesquisa.get("n_entrevistados") or pesquisa.get("entrevistados"),
            "margem_erro": pesquisa.get("margem_erro") or pesquisa.get("margemErro"),
            "uf": pesquisa.get("uf", "BR"),
            "cd_cargo": cargo,
            "ano": year,
            "fonte": "poder360",
            "record_confidence_score": 0.70,
        }
        for candidato in pesquisa.get("candidatos", pesquisa.get("resultados", [])):
            row = {
                **base,
                "candidato": candidato.get("nome") or candidato.get("candidato"),
                "intencao_pct": candidato.get("intencao") or candidato.get("percentual"),
            }
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["data_pesquisa_inicio"] = pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce")
    df["data_pesquisa_fim"] = pd.to_datetime(df["data_pesquisa_fim"], errors="coerce")
    df["intencao_pct"] = pd.to_numeric(df["intencao_pct"], errors="coerce")
    df["n_entrevistados"] = pd.to_numeric(df["n_entrevistados"], errors="coerce")
    df["ano"] = year

    if "data_pesquisa_fim" in df.columns:
        df = df[df["data_pesquisa_fim"].dt.year == year]

    logger.info("Poder360: %d linhas scrapeadas para ano=%d cargo=%s", len(df), year, cargo)
    return df


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
