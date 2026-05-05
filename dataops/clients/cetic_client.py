"""CETIC substitute — IBGE Censo Demográfico 2022: domicílios com internet por UF.

Substitui a tabela PNAD TIC (9174) que está com instabilidade no SIDRA v3.
O Censo 2022 oferece cobertura completa (todos os 27 estados) com dados superiores.

Source: IBGE SIDRA tabela 9936 via apisidra.ibge.gov.br (público, sem autenticação).
Classificação 2072/77585 = "Existência de conexão domiciliar à Internet = Sim".
"""

from __future__ import annotations

import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.cetic")

_APISIDRA_BASE = "https://apisidra.ibge.gov.br/values"

# Tabela 9936: Domicílios particulares permanentes ocupados, por existência de
# conexão domiciliar à Internet — Censo Demográfico 2022
# c2072/77585 = Existência de conexão = Sim
# c63/95826   = Condição de ocupação = Total
# c125/2932   = Tipo de domicílio = Total
_CENSO_URL = (
    f"{_APISIDRA_BASE}/t/9936/n3/all/v/381,1000381/p/2022"
    "/c2072/77585/c63/95826/c125/2932"
)

# Código IBGE da UF (D1C) → sigla
_IBGE_CODE_UF: dict[str, str] = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA",
    "16": "AP", "17": "TO", "21": "MA", "22": "PI", "23": "CE",
    "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE",
    "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT",
    "52": "GO", "53": "DF",
}

# Cached result — the Censo data is static, avoid redundant network calls
_censo_cache: dict[str, dict] | None = None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_censo_internet() -> list[dict]:
    resp = requests.get(_CENSO_URL, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data[1:] if data else []


def _load_censo_cache() -> dict[str, dict]:
    global _censo_cache
    if _censo_cache is not None:
        return _censo_cache

    rows = _fetch_censo_internet()
    result: dict[str, dict] = {}
    for r in rows:
        sg_uf = _IBGE_CODE_UF.get(r.get("D1C", ""))
        if not sg_uf:
            continue
        if sg_uf not in result:
            result[sg_uf] = {"pct_internet_domiciliar": None, "total_domicilios_com_internet": None}
        var_code = r.get("D2C", "")
        try:
            v = float(r["V"]) if r.get("V") not in (None, "", "...") else None
        except (TypeError, ValueError):
            v = None
        if var_code == "381":
            result[sg_uf]["total_domicilios_com_internet"] = v
        elif var_code == "1000381":
            result[sg_uf]["pct_internet_domiciliar"] = v

    _censo_cache = result
    logger.info("Censo 2022 internet: %d UFs carregadas", len(result))
    return result


def fetch_cetic_indicators(uf: str, year: int) -> dict[str, float | None]:
    """Return digital access indicators for a UF from Censo 2022.

    The year parameter is accepted for interface compatibility; the Censo snapshot
    is from 2022 regardless of the requested year.
    """
    try:
        uf_data = _load_censo_cache()
    except Exception as exc:
        logger.warning("Censo 2022 internet fetch falhou: %s", exc)
        return {"pct_internet_domiciliar": None, "pct_computador_domiciliar": None, "pct_smartphone_domiciliar": None}

    indicators = uf_data.get(uf.upper(), {})
    result = {
        "pct_internet_domiciliar": indicators.get("pct_internet_domiciliar"),
        "pct_computador_domiciliar": None,
        "pct_smartphone_domiciliar": None,
    }
    logger.info(
        "Censo 2022 internet UF=%s: %.1f%%",
        uf.upper(),
        result["pct_internet_domiciliar"] or 0,
    )
    return result


def build_digital_access_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build DataFrame with digital access indicators for all municipalities.

    Uses Censo Demográfico 2022; values propagated to all municipalities in UF.
    """
    indicators = fetch_cetic_indicators(uf, year)

    if indicators.get("pct_internet_domiciliar") is None:
        logger.warning("Sem dado Censo 2022 internet para UF=%s", uf)
        return pd.DataFrame()

    rows = [
        {
            "cd_municipio_ibge": cd,
            "sg_uf": uf.upper(),
            "ano": 2022,
            **indicators,
            "granularidade": "UF",
            "fontes": "IBGE Censo Demográfico 2022 (tabela 9936)",
        }
        for cd in municipios_ibge
    ]
    return pd.DataFrame(rows)
