"""Sanções federais — CEIS e CNEP via Portal da Transparência.

CEIS: Cadastro de Empresas Inidôneas e Suspensas
CNEP: Cadastro Nacional de Empresas Punidas

Endpoint: https://api.portaldatransparencia.gov.br/api-de-dados/ceis
          https://api.portaldatransparencia.gov.br/api-de-dados/cnep
Requer: TRANSPARENCIA_API_KEY

Permite cruzar sancionados (PF + PJ) × candidatos TSE por nome + UF
para análise de "eleitores punem ficha suja?".
"""

from __future__ import annotations

import logging
import os

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.sancoes")

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


def _fetch_paginated(endpoint: str, extra_params: dict | None = None) -> list[dict]:
    url = f"{_BASE}/{endpoint}"
    rows: list[dict] = []
    pagina = 1
    params = dict(extra_params or {})
    while True:
        try:
            data = _get(url, {**params, "pagina": pagina})
        except Exception as exc:
            logger.warning("%s pág %d: %s", endpoint, pagina, exc)
            break
        if not isinstance(data, list):
            logger.warning("%s pág %d: resposta inesperada (tipo=%s): %s", endpoint, pagina, type(data).__name__, str(data)[:200])
            break
        if not data:
            break
        rows.extend(data)
        logger.info("%s: pág %d → %d registros", endpoint, pagina, len(rows))
        if len(data) < _PAGE_SIZE:
            break
        pagina += 1
    return rows


def _normalize_sancao(item: dict, origem: str) -> dict:
    """Flatten a sanção record to a flat dict."""
    sancionado = item.get("sancionado") or {}
    orgao = item.get("orgaoSancionador") or {}
    return {
        "origem": origem,
        "nm_sancionado": (sancionado.get("nome") or "").strip().upper(),
        "nr_cpf_cnpj": str(sancionado.get("cpfCnpj") or "")
        .replace(".", "")
        .replace("-", "")
        .replace("/", ""),
        "tp_pessoa": "PF"
        if len(str(sancionado.get("cpfCnpj") or "").replace(".", "").replace("-", "")) == 11
        else "PJ",
        "sg_uf_sancionado": sancionado.get("uf") or "",
        "nm_orgao_sancionador": orgao.get("nome") or "",
        "sg_uf_orgao": orgao.get("uf") or "",
        "tp_sancao": item.get("tipoSancao") or "",
        "dt_inicio_sancao": str(item.get("dataInicioSancao") or ""),
        "dt_fim_sancao": str(item.get("dataFimSancao") or ""),
        "ds_fundamentacao": item.get("fundamentacaoLegal") or "",
        "nm_processo": item.get("numeroProcesso") or "",
        "fonte": "portal_transparencia",
    }


def fetch_ceis() -> pd.DataFrame:
    """Fetch CEIS — Cadastro de Empresas Inidôneas e Suspensas (PF + PJ)."""
    logger.info("Buscando CEIS...")
    rows = _fetch_paginated("ceis")
    if not rows:
        logger.warning("CEIS vazio")
        return pd.DataFrame()
    df = pd.DataFrame([_normalize_sancao(r, "CEIS") for r in rows])
    logger.info("CEIS: %d registros (%d PF)", len(df), (df["tp_pessoa"] == "PF").sum())
    return df


def fetch_cnep() -> pd.DataFrame:
    """Fetch CNEP — Cadastro Nacional de Empresas Punidas (improbidade, LIA)."""
    logger.info("Buscando CNEP...")
    rows = _fetch_paginated("cnep")
    if not rows:
        logger.warning("CNEP vazio")
        return pd.DataFrame()
    df = pd.DataFrame([_normalize_sancao(r, "CNEP") for r in rows])
    logger.info("CNEP: %d registros (%d PF)", len(df), (df["tp_pessoa"] == "PF").sum())
    return df


def fetch_sancoes() -> pd.DataFrame:
    """Fetch CEIS + CNEP combined, deduplicated by CPF/CNPJ + origem."""
    df_ceis = fetch_ceis()
    df_cnep = fetch_cnep()

    frames = [f for f in [df_ceis, df_cnep] if not f.empty]
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["nr_cpf_cnpj", "origem", "tp_sancao", "dt_inicio_sancao"])
    logger.info(
        "Sanções total: %d registros únicos (%d PF)",
        len(df),
        (df["tp_pessoa"] == "PF").sum(),
    )
    return df
