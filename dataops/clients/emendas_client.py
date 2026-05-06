"""Emendas Parlamentares — Portal da Transparência API.

Fonte: https://api.portaldatransparencia.gov.br/api-de-dados/emendas-parlamentares
Requer: TRANSPARENCIA_API_KEY

Campos retornados por município:
  - valor empenhado, liquidado, pago
  - parlamentar (nome, partido, UF, cargo)
  - tipo emenda (individual, bancada, comissão, relator)
  - área temática (saúde, educação, infraestrutura, etc.)
  - ano orçamentário
"""

from __future__ import annotations

import logging
import os

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.emendas")

_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
_PAGE_SIZE = 500


def _headers() -> dict[str, str]:
    key = os.environ.get("TRANSPARENCIA_API_KEY", "")
    if not key:
        raise RuntimeError("TRANSPARENCIA_API_KEY não configurada")
    return {"chave-api-dados": key}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _get(url: str, params: dict) -> list[dict]:
    resp = requests.get(url, params=params, headers=_headers(), timeout=60)
    resp.raise_for_status()
    return resp.json()


def _fetch_emendas_municipio(year: int, codigo_ibge: str) -> list[dict]:
    """Fetch emendas for a single município (all pages)."""
    url = f"{_BASE}/emendas-parlamentares"
    rows: list[dict] = []
    pagina = 1
    while True:
        try:
            data = _get(url, {"ano": year, "codigoIbge": codigo_ibge, "pagina": pagina})
        except Exception as exc:
            logger.warning("Emendas %s/%d pág %d: %s", codigo_ibge, year, pagina, exc)
            break
        if not data:
            break
        rows.extend(data)
        if len(data) < _PAGE_SIZE:
            break
        pagina += 1
    return rows


def _fetch_emendas_year(year: int) -> pd.DataFrame:
    """Fetch all emendas parlamentares for a year (paginated national endpoint)."""
    url = f"{_BASE}/emendas-parlamentares"
    rows: list[dict] = []
    pagina = 1
    while True:
        try:
            data = _get(url, {"ano": year, "pagina": pagina})
        except Exception as exc:
            logger.warning("Emendas ano=%d pág %d: %s", year, pagina, exc)
            break
        if not isinstance(data, list):
            logger.warning(
                "Emendas ano=%d pág %d: resposta inesperada (tipo=%s): %s",
                year,
                pagina,
                type(data).__name__,
                str(data)[:200],
            )
            break
        if not data:
            break
        rows.extend(data)
        logger.info("Emendas %d: pág %d → %d registros acumulados", year, pagina, len(rows))
        if len(data) < _PAGE_SIZE:
            break
        pagina += 1

    if not rows:
        return pd.DataFrame()

    return _normalize(rows, year)


def _normalize(rows: list[dict], year: int) -> pd.DataFrame:
    records = []
    for item in rows:
        mun = item.get("municipio") or {}
        parlamentar = item.get("autor") or {}
        records.append(
            {
                "ano": year,
                "cd_municipio_ibge": str(mun.get("codigoIbge", "") or "")[:7],
                "nm_municipio": mun.get("nomeIbge", "") or mun.get("nome", ""),
                "sg_uf": mun.get("uf", "") or "",
                "nm_parlamentar": parlamentar.get("nome", ""),
                "sg_partido": parlamentar.get("siglaPartido", ""),
                "sg_uf_parlamentar": parlamentar.get("uf", ""),
                "ds_cargo_parlamentar": parlamentar.get("cargo", ""),
                "tp_emenda": item.get("tipoEmenda", ""),
                "ds_area": item.get("funcao", {}).get("descricao", "")
                if isinstance(item.get("funcao"), dict)
                else "",
                "ds_subfuncao": item.get("subfuncao", {}).get("descricao", "")
                if isinstance(item.get("subfuncao"), dict)
                else "",
                "vl_empenhado": float(item.get("valorEmpenhado") or 0),
                "vl_liquidado": float(item.get("valorLiquidado") or 0),
                "vl_pago": float(item.get("valorPago") or 0),
                "nr_emenda": str(item.get("numero", "") or ""),
                "fonte": "portal_transparencia_emendas",
            }
        )
    df = pd.DataFrame(records)
    df["cd_municipio_ibge"] = df["cd_municipio_ibge"].str[:7]
    return df


def fetch_emendas_parlamentares(year: int) -> pd.DataFrame:
    """Fetch all emendas parlamentares for a year, national scope.

    Returns one row per emenda × município with parlamentar, area, and values.
    """
    logger.info("Emendas parlamentares: ano=%d", year)
    df = _fetch_emendas_year(year)
    if df.empty:
        logger.warning("Sem emendas para ano=%d", year)
    else:
        logger.info(
            "Emendas %d: %d registros, %d municípios únicos, R$ %.1fM pago",
            year,
            len(df),
            df["cd_municipio_ibge"].nunique(),
            df["vl_pago"].sum() / 1e6,
        )
    return df
