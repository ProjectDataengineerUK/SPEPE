"""Sanções federais — CEIS, CNEP, CEAF, CEPIM via Portal da Transparência.

CEIS : Cadastro de Empresas Inidôneas e Suspensas       (PF + PJ)
CNEP : Cadastro Nacional de Empresas Punidas            (improbidade, LIA)
CEAF : Cadastro de Expulsões da Administração Federal   (servidores demitidos/exonerados)
CEPIM: Entidades Privadas sem Fins Lucrativos Impedidas (ONGs bloqueadas de convênios)

Endpoints: https://api.portaldatransparencia.gov.br/api-de-dados/{ceis,cnep,ceaf,cepim}
Requer: TRANSPARENCIA_API_KEY

Uso eleitoral:
  CEIS/CNEP: cruzar sancionados × candidatos TSE por nome+UF ("ficha suja")
  CEAF     : identificar servidores expulsos que tentam voltar via mandato
  CEPIM    : ONGs associadas a candidatos que estão impedidas de receber verbas
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
            logger.warning(
                "%s pág %d: resposta inesperada (tipo=%s): %s",
                endpoint,
                pagina,
                type(data).__name__,
                str(data)[:200],
            )
            break
        if not data:
            break
        rows.extend(data)
        logger.info("%s: pág %d → %d registros", endpoint, pagina, len(rows))
        if len(data) < _PAGE_SIZE:
            break
        pagina += 1
    return rows


def _extract_str(val: object, key: str = "descricao") -> str:
    """Extract string from a value that may be a dict, list, or scalar."""
    if isinstance(val, dict):
        return str(val.get(key) or val.get("nome") or val.get("sigla") or val.get("id") or "")
    if isinstance(val, list):
        return ", ".join(_extract_str(v, key) for v in val)
    return str(val or "")


# ── CEIS ─────────────────────────────────────────────────────────────────────


def _normalize_ceis_cnep(item: dict, origem: str) -> dict:
    """Flatten CEIS/CNEP record — handles nested API dicts."""
    sancionado = item.get("sancionado") or {}
    if isinstance(sancionado, str):
        sancionado = {}
    orgao = item.get("orgaoSancionador") or {}
    if isinstance(orgao, str):
        orgao = {}

    cpf_cnpj = (
        str(sancionado.get("cpfCnpj") or "").replace(".", "").replace("-", "").replace("/", "")
    )
    return {
        "origem": origem,
        "nm_sancionado": (sancionado.get("nome") or "").strip().upper(),
        "nr_cpf_cnpj": cpf_cnpj,
        "tp_pessoa": "PF" if len(cpf_cnpj) == 11 else "PJ",
        "sg_uf_sancionado": _extract_str(sancionado.get("uf"), "sigla"),
        "nm_orgao_sancionador": _extract_str(orgao.get("nome") or orgao),
        "sg_uf_orgao": _extract_str(orgao.get("uf"), "sigla"),
        "tp_sancao": _extract_str(item.get("tipoSancao")),
        "dt_inicio_sancao": str(item.get("dataInicioSancao") or ""),
        "dt_fim_sancao": str(item.get("dataFimSancao") or ""),
        "ds_fundamentacao": _extract_str(item.get("fundamentacaoLegal")),
        "nm_processo": str(item.get("numeroProcesso") or ""),
        "fonte": "portal_transparencia",
    }


def fetch_ceis() -> pd.DataFrame:
    """Fetch CEIS — Cadastro de Empresas Inidôneas e Suspensas (PF + PJ)."""
    logger.info("Buscando CEIS...")
    rows = _fetch_paginated("ceis")
    if not rows:
        logger.warning("CEIS vazio")
        return pd.DataFrame()
    df = pd.DataFrame([_normalize_ceis_cnep(r, "CEIS") for r in rows])
    logger.info("CEIS: %d registros (%d PF)", len(df), (df["tp_pessoa"] == "PF").sum())
    return df


def fetch_cnep() -> pd.DataFrame:
    """Fetch CNEP — Cadastro Nacional de Empresas Punidas (improbidade, LIA)."""
    logger.info("Buscando CNEP...")
    rows = _fetch_paginated("cnep")
    if not rows:
        logger.warning("CNEP vazio")
        return pd.DataFrame()
    df = pd.DataFrame([_normalize_ceis_cnep(r, "CNEP") for r in rows])
    logger.info("CNEP: %d registros (%d PF)", len(df), (df["tp_pessoa"] == "PF").sum())
    return df


# ── CEAF ─────────────────────────────────────────────────────────────────────


def _normalize_ceaf(item: dict) -> dict:
    """Flatten CEAF (expulsões de servidores) record."""
    servidor = item.get("servidor") or {}
    if isinstance(servidor, str):
        servidor = {}
    orgao = item.get("orgaoLotacao") or item.get("orgao") or {}
    if isinstance(orgao, str):
        orgao = {}

    cpf = (
        str(servidor.get("cpf") or servidor.get("cpfCnpj") or "").replace(".", "").replace("-", "")
    )
    return {
        "origem": "CEAF",
        "nm_sancionado": (servidor.get("nome") or "").strip().upper(),
        "nr_cpf_cnpj": cpf,
        "tp_pessoa": "PF",
        "sg_uf_sancionado": _extract_str(servidor.get("uf"), "sigla"),
        "nm_orgao_sancionador": _extract_str(orgao.get("nome") or orgao),
        "sg_uf_orgao": _extract_str(orgao.get("uf"), "sigla"),
        "tp_sancao": _extract_str(item.get("tipoExpulsao") or item.get("tipoPunicao")),
        "dt_inicio_sancao": str(item.get("dataExpulsao") or item.get("dataPublicacao") or ""),
        "dt_fim_sancao": "",
        "ds_fundamentacao": _extract_str(item.get("fundamentacaoLegal")),
        "nm_processo": str(item.get("numeroProcesso") or ""),
        "nm_cargo": str(servidor.get("cargo") or ""),
        "nm_orgao_lotacao": _extract_str(orgao),
        "fonte": "portal_transparencia",
    }


def fetch_ceaf() -> pd.DataFrame:
    """Fetch CEAF — Cadastro de Expulsões da Administração Federal (servidores)."""
    logger.info("Buscando CEAF...")
    rows = _fetch_paginated("ceaf")
    if not rows:
        logger.warning("CEAF vazio")
        return pd.DataFrame()
    df = pd.DataFrame([_normalize_ceaf(r) for r in rows])
    logger.info("CEAF: %d registros de servidores expulsos", len(df))
    return df


# ── CEPIM ─────────────────────────────────────────────────────────────────────


def _normalize_cepim(item: dict) -> dict:
    """Flatten CEPIM (entidades sem fins lucrativos impedidas) record."""
    entidade = item.get("entidade") or {}
    if isinstance(entidade, str):
        entidade = {}
    orgao_superior = item.get("orgaoSuperior") or {}
    if isinstance(orgao_superior, str):
        orgao_superior = {}

    cnpj = (
        str(entidade.get("cnpj") or entidade.get("cpfCnpj") or "")
        .replace(".", "")
        .replace("-", "")
        .replace("/", "")
    )
    return {
        "origem": "CEPIM",
        "nm_sancionado": (entidade.get("nome") or entidade.get("razaoSocial") or "")
        .strip()
        .upper(),
        "nr_cpf_cnpj": cnpj,
        "tp_pessoa": "PJ",
        "sg_uf_sancionado": _extract_str(entidade.get("uf"), "sigla"),
        "nm_orgao_sancionador": _extract_str(orgao_superior.get("nome") or orgao_superior),
        "sg_uf_orgao": "",
        "tp_sancao": _extract_str(item.get("motivoImpedimento") or item.get("tipoImpedimento")),
        "dt_inicio_sancao": str(item.get("dataInicioImpedimento") or item.get("dataInicio") or ""),
        "dt_fim_sancao": str(item.get("dataFimImpedimento") or item.get("dataFim") or ""),
        "ds_fundamentacao": _extract_str(item.get("fundamentacaoLegal")),
        "nm_processo": str(item.get("numeroConvenio") or item.get("numeroProcesso") or ""),
        "fonte": "portal_transparencia",
    }


def fetch_cepim() -> pd.DataFrame:
    """Fetch CEPIM — Entidades Privadas sem Fins Lucrativos Impedidas de receber verbas."""
    logger.info("Buscando CEPIM...")
    rows = _fetch_paginated("cepim")
    if not rows:
        logger.warning("CEPIM vazio")
        return pd.DataFrame()
    df = pd.DataFrame([_normalize_cepim(r) for r in rows])
    logger.info("CEPIM: %d entidades impedidas", len(df))
    return df


# ── Consolidação ───────────────────────────────────────────────────────────────


def fetch_sancoes() -> pd.DataFrame:
    """Fetch CEIS + CNEP + CEAF + CEPIM combined, deduplicated.

    Returns unified DataFrame with one row per sanção, campo `origem` identifica
    a base de origem: CEIS | CNEP | CEAF | CEPIM.
    """
    df_ceis = fetch_ceis()
    df_cnep = fetch_cnep()
    df_ceaf = fetch_ceaf()
    df_cepim = fetch_cepim()

    frames = [f for f in [df_ceis, df_cnep, df_ceaf, df_cepim] if not f.empty]
    if not frames:
        logger.warning("Todas as bases de sanções retornaram vazio")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Garantir que colunas de dedup não tenham tipos não-hasháveis
    for col in ("nr_cpf_cnpj", "origem", "tp_sancao", "dt_inicio_sancao"):
        if col in df.columns:
            df[col] = df[col].astype(str).fillna("")

    df = df.drop_duplicates(subset=["nr_cpf_cnpj", "origem", "tp_sancao", "dt_inicio_sancao"])

    logger.info(
        "Sanções total: %d registros únicos | CEIS=%d CNEP=%d CEAF=%d CEPIM=%d",
        len(df),
        (df["origem"] == "CEIS").sum(),
        (df["origem"] == "CNEP").sum(),
        (df["origem"] == "CEAF").sum(),
        (df["origem"] == "CEPIM").sum(),
    )
    return df
