"""CadÚnico + Programas Sociais — dados agregados por município.

Endpoints acessíveis com key básica do Portal da Transparência:
  A) /novo-bolsa-familia-por-municipio  — Novo BF (2023+) por município/mês
  B) /bpc-por-municipio                 — BPC por município/mês
  C) /auxilio-emergencial-por-municipio — Aux. Emergencial por município
  D) SAGI/MI Social CSV                 — famílias CadÚnico por município (sem auth)

Para anos históricos (2018, 2022): tenta /bolsa-familia-por-municipio;
se retornar 403 (nível insuficiente), usa SAGI como fallback.
"""

from __future__ import annotations

import io
import logging
import os
import time

import pandas as pd
import requests
from tenacity import RetryError, retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.cadunico")

_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
_PAGE_SIZE = 500
_SKIP_STATUS = frozenset({403, 404, 405})


def _headers() -> dict[str, str]:
    key = os.environ.get("TRANSPARENCIA_API_KEY", "")
    if not key:
        logger.warning("TRANSPARENCIA_API_KEY não configurada")
    return {"chave-api-dados": key} if key else {}


def _is_retryable(exc: BaseException) -> bool:
    """Only retry on 5xx or network errors — never on 4xx client errors."""
    if isinstance(exc, requests.HTTPError):
        return exc.response is None or exc.response.status_code >= 500
    return True


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_retryable),
)
def _get(url: str, params: dict) -> list[dict]:
    resp = requests.get(url, params=params, headers=_headers(), timeout=60)
    resp.raise_for_status()
    return resp.json()


def _get_safe(url: str, params: dict) -> list[dict]:
    """Like _get but returns [] on 403/404/405 instead of raising."""
    try:
        return _get(url, params)
    except (requests.HTTPError, RetryError) as exc:
        status: int | None = None
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            status = exc.response.status_code
        elif isinstance(exc, RetryError):
            inner = exc.last_attempt.exception()
            if isinstance(inner, requests.HTTPError) and inner.response is not None:
                status = inner.response.status_code
        if status in _SKIP_STATUS:
            logger.warning("Endpoint indisponível (HTTP %d): %s", status, url)
            return []
        raise


def _uf_from(val: object) -> str:
    if isinstance(val, dict):
        return val.get("sigla") or val.get("uf") or ""
    return str(val or "")


def _fetch_paginated_municipio(endpoint: str, mes_ano: str) -> list[dict]:
    """Generic paginated fetch for endpoints that accept mesAno + pagina."""
    url = f"{_BASE}/{endpoint}"
    rows: list[dict] = []
    pagina = 1
    while True:
        data = _get_safe(url, {"mesAno": mes_ano, "pagina": pagina})
        if not isinstance(data, list) or not data:
            break
        rows.extend(data)
        logger.info("%s mesAno=%s pág %d → %d registros", endpoint, mes_ano, pagina, len(rows))
        if len(data) < _PAGE_SIZE:
            break
        pagina += 1
        time.sleep(0.5)
    return rows


# ── Novo Bolsa Família (2023+) ────────────────────────────────────────────────


def _fetch_novo_bolsa_familia(year: int) -> pd.DataFrame:
    """Fetch Novo Bolsa Família por município (dezembro do ano). Válido a partir de 2023."""
    mes_ano = f"{year}12"
    rows = _fetch_paginated_municipio("novo-bolsa-familia-por-municipio", mes_ano)
    if not rows:
        return pd.DataFrame()

    records = []
    for item in rows:
        mun = item.get("municipio") or {}
        records.append(
            {
                "cd_municipio_ibge": str(mun.get("codigoIBGE") or "")[:7],
                "nm_municipio": mun.get("nomeIBGE") or mun.get("nome") or "",
                "sg_uf": _uf_from(mun.get("uf")),
                "ano": year,
                "mes": 12,
                "qtd_beneficiarios_novo_bolsa_familia": int(
                    item.get("quantidadeBeneficiados") or 0
                ),
                "valor_total_novo_bolsa_familia_reais": float(item.get("valor") or 0),
                "fonte": "portal_transparencia_novo_bf",
            }
        )

    df = pd.DataFrame(records)
    df["cd_municipio_ibge"] = df["cd_municipio_ibge"].str[:7]
    logger.info("Novo Bolsa Família %d: %d municípios", year, len(df))
    return df


# ── Bolsa Família histórico (pré-2023) ────────────────────────────────────────


def _fetch_bolsa_familia_historico(year: int) -> pd.DataFrame:
    """Fetch Bolsa Família por município (dezembro do ano). Pode retornar 403 com key básica."""
    mes_ano = f"{year}12"
    rows = _fetch_paginated_municipio("bolsa-familia-por-municipio", mes_ano)
    if not rows:
        return pd.DataFrame()

    records = []
    for item in rows:
        mun = item.get("municipio") or {}
        records.append(
            {
                "cd_municipio_ibge": str(mun.get("codigoIBGE") or "")[:7],
                "nm_municipio": mun.get("nomeIBGE") or mun.get("nome") or "",
                "sg_uf": _uf_from(mun.get("uf")),
                "ano": year,
                "mes": 12,
                "qtd_beneficiarios_bolsa_familia": int(item.get("quantidadeBeneficiados") or 0),
                "valor_total_bolsa_familia_reais": float(item.get("valor") or 0),
                "fonte": "portal_transparencia_bf",
            }
        )

    df = pd.DataFrame(records)
    df["cd_municipio_ibge"] = df["cd_municipio_ibge"].str[:7]
    logger.info("Bolsa Família histórico %d: %d municípios", year, len(df))
    return df


# ── BPC por município ──────────────────────────────────────────────────────────


def _fetch_bpc(year: int) -> pd.DataFrame:
    """Fetch BPC (Benefício de Prestação Continuada) por município (dezembro do ano)."""
    mes_ano = f"{year}12"
    rows = _fetch_paginated_municipio("bpc-por-municipio", mes_ano)
    if not rows:
        return pd.DataFrame()

    records = []
    for item in rows:
        mun = item.get("municipio") or {}
        records.append(
            {
                "cd_municipio_ibge": str(mun.get("codigoIBGE") or "")[:7],
                "nm_municipio": mun.get("nomeIBGE") or mun.get("nome") or "",
                "sg_uf": _uf_from(mun.get("uf")),
                "ano": year,
                "mes": 12,
                "qtd_beneficiarios_bpc": int(item.get("quantidadeBeneficiados") or 0),
                "valor_total_bpc_reais": float(item.get("valor") or 0),
                "fonte": "portal_transparencia_bpc",
            }
        )

    df = pd.DataFrame(records)
    df["cd_municipio_ibge"] = df["cd_municipio_ibge"].str[:7]
    logger.info("BPC %d: %d municípios", year, len(df))
    return df


# ── Auxílio Emergencial por município ──────────────────────────────────────────


def _fetch_auxilio_emergencial(year: int) -> pd.DataFrame:
    """Fetch Auxílio Emergencial por município. Disponível para 2020 e 2021."""
    if year not in (2020, 2021):
        return pd.DataFrame()

    mes_ano = f"{year}12"
    rows = _fetch_paginated_municipio("auxilio-emergencial-por-municipio", mes_ano)
    if not rows:
        return pd.DataFrame()

    records = []
    for item in rows:
        mun = item.get("municipio") or {}
        records.append(
            {
                "cd_municipio_ibge": str(mun.get("codigoIBGE") or "")[:7],
                "nm_municipio": mun.get("nomeIBGE") or mun.get("nome") or "",
                "sg_uf": _uf_from(mun.get("uf")),
                "ano": year,
                "mes": 12,
                "qtd_beneficiarios_auxilio_emergencial": int(
                    item.get("quantidadeBeneficiados") or 0
                ),
                "valor_total_auxilio_emergencial_reais": float(item.get("valor") or 0),
                "fonte": "portal_transparencia_aux_emergencial",
            }
        )

    df = pd.DataFrame(records)
    df["cd_municipio_ibge"] = df["cd_municipio_ibge"].str[:7]
    logger.info("Auxílio Emergencial %d: %d municípios", year, len(df))
    return df


# ── SAGI / MI Social (fallback sem auth) ──────────────────────────────────────


def _fetch_cadunico_sagi(year: int) -> pd.DataFrame:
    """Fetch famílias CadÚnico por município via SAGI/MI Social (sem autenticação)."""
    url = "https://aplicacoes.mds.gov.br/sagi/vis/data3/v.php"
    params = {
        "q[]": "getdata",
        "t[]": "cadunico_municipio",
        "filtros[]": f"ano_ref={year}",
        "campos[]": "id_municipio,qtd_familias_cadunico,qtd_familias_pobreza,qtd_familias_extrema_pobreza",
        "formato": "csv",
    }
    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), sep=";", dtype=str)
        if df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "id_municipio": "cd_municipio_ibge",
                "qtd_familias_pobreza": "qtd_familias_baixa_renda",
            }
        )
        df["ano"] = year
        df["fonte"] = "sagi_mds"

        for col in (
            "qtd_familias_cadunico",
            "qtd_familias_baixa_renda",
            "qtd_familias_extrema_pobreza",
        ):
            if col in df.columns:
                df[col] = (
                    pd.to_numeric(df[col].str.replace(",", "."), errors="coerce")
                    .fillna(0)
                    .astype(int)
                )

        logger.info("SAGI CadÚnico %d: %d municípios", year, len(df))
        return df
    except Exception as exc:
        logger.warning("SAGI CadÚnico %d falhou: %s", year, exc)
        return pd.DataFrame()


# ── Consolidação ───────────────────────────────────────────────────────────────


def fetch_cadunico_municipio(year: int) -> pd.DataFrame:
    """Fetch consolidated social programs data by municipality for a year.

    Strategy by year:
      2023+  : Novo Bolsa Família por município + BPC
      2018, 2022: tenta BF histórico (pode ser 403) + BPC + SAGI fallback
      2020, 2021: Auxílio Emergencial + BPC + SAGI

    Returns one row per município with social program indicators.
    """
    frames: list[pd.DataFrame] = []

    # ── Programas de transferência de renda ───────────────────────────────────
    if year >= 2023:
        df_nbf = _fetch_novo_bolsa_familia(year)
        if not df_nbf.empty:
            frames.append(df_nbf)
    else:
        df_bf = _fetch_bolsa_familia_historico(year)
        if not df_bf.empty:
            frames.append(df_bf)

    # ── BPC (disponível para todos os anos) ───────────────────────────────────
    df_bpc = _fetch_bpc(year)
    if not df_bpc.empty:
        frames.append(df_bpc)

    # ── Auxílio Emergencial (2020-2021) ───────────────────────────────────────
    df_ae = _fetch_auxilio_emergencial(year)
    if not df_ae.empty:
        frames.append(df_ae)

    # ── SAGI fallback (famílias CadÚnico) ────────────────────────────────────
    df_sagi = _fetch_cadunico_sagi(year)

    # ── Merge tudo em um DataFrame por município ──────────────────────────────
    if not frames and df_sagi.empty:
        logger.warning("Sem dados de programas sociais para ano=%d", year)
        return pd.DataFrame()

    if frames:
        # Merge all frames side by side on cd_municipio_ibge
        base = frames[0].copy()
        for df_extra in frames[1:]:
            merge_cols = [c for c in df_extra.columns if c != "fonte"]
            base = base.merge(
                df_extra[merge_cols],
                on=["cd_municipio_ibge", "ano"],
                how="outer",
                suffixes=("", "_dup"),
            )
            # deduplicate nm_municipio and sg_uf if duplicated by outer join
            for col in ("nm_municipio", "sg_uf"):
                dup = f"{col}_dup"
                if dup in base.columns:
                    base[col] = base[col].fillna(base[dup])
                    base.drop(columns=[dup], inplace=True)
    else:
        base = pd.DataFrame()

    if not df_sagi.empty:
        sagi_cols = [
            c
            for c in df_sagi.columns
            if c not in ("fonte", "nm_municipio", "sg_uf") or c == "cd_municipio_ibge"
        ]
        if base.empty:
            base = df_sagi
        else:
            base = base.merge(
                df_sagi[sagi_cols],
                on=["cd_municipio_ibge", "ano"],
                how="left",
            )

    base["fonte"] = "portal_transparencia+sagi_mds"
    base["cd_municipio_ibge"] = base["cd_municipio_ibge"].astype(str).str[:7]

    logger.info("Programas sociais consolidados %d: %d municípios", year, len(base))
    return base
