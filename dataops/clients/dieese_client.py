"""DIEESE client — Cesta Básica Nacional (preço mensal por capital de UF)."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.dieese")

# DIEESE publishes monthly CSVs — available via their public data portal
_DIEESE_CESTA_URL = (
    "https://www.dieese.org.br/analise/web/remuneracao/"
    "processamentoRemuneracaoRetornoCSV.do"
    "?tipo=1&coduf=0&codmun=0&ano={year}&mes=12"
)

# Fallback: IPEADATA has DIEESE cesta básica series at state level
_IPEADATA_DIEESE_BASE = (
    "http://www.ipeadata.gov.br/api/odata4/ValoresSerie"
    "(SERCODIGO='DIEESE_CBASIC{uf}')?$top=24&$orderby=VALDATA%20desc"
)

# Map UF → capital IBGE code (for annual reference price)
_UF_CAPITAL_IBGE: dict[str, int] = {
    "AC": 1200401, "AL": 2704302, "AM": 1302603, "AP": 1600303,
    "BA": 2927408, "CE": 2304400, "DF": 5300108, "ES": 3205309,
    "GO": 5208707, "MA": 2111300, "MG": 3106200, "MS": 5002704,
    "MT": 5103403, "PA": 1501402, "PB": 2507507, "PE": 2611606,
    "PI": 2211001, "PR": 4106902, "RJ": 3304557, "RN": 2408102,
    "RO": 1100205, "RR": 1400100, "RS": 4314902, "SC": 4205407,
    "SE": 2800308, "SP": 3550308, "TO": 1721000,
}

# DIEESE keeps separate series codes per city — SP as reference
_DIEESE_IPEADATA_SERIES = "DIEESE_CESTBASICA{uf}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get_json(url: str) -> dict:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=5, max=30))
def _get_csv(url: str, **kwargs) -> pd.DataFrame:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return pd.read_csv(BytesIO(resp.content), **kwargs)


def fetch_cesta_basica_uf(uf: str, year: int) -> dict[str, float | None]:
    """Return cesta básica indicators for a UF in a given year.

    Returns dict with keys:
      - cesta_basica_capital_brl
      - variacao_cesta_mensal_pct
      - horas_trabalho_cesta
    """
    uf_upper = uf.upper()
    result: dict[str, float | None] = {
        "cesta_basica_capital_brl": None,
        "variacao_cesta_mensal_pct": None,
        "horas_trabalho_cesta": None,
    }

    serie = _DIEESE_IPEADATA_SERIES.format(uf=uf_upper)
    url = _IPEADATA_DIEESE_BASE.format(uf=uf_upper)
    url = url.replace(f"DIEESE_CBASIC{uf_upper}", serie)

    try:
        data = _get_json(url)
        entries = data.get("value", [])
    except Exception as exc:
        logger.warning("IPEADATA DIEESE fetch failed for UF=%s: %s", uf, exc)
        entries = []

    year_entries = [
        e for e in entries if str(year) in str(e.get("VALDATA", ""))
    ]
    if year_entries:
        vals = []
        for e in year_entries:
            try:
                vals.append(float(e["VALVALOR"]))
            except (TypeError, ValueError, KeyError):
                pass
        if vals:
            result["cesta_basica_capital_brl"] = round(sum(vals) / len(vals), 2)

    logger.info(
        "DIEESE cesta básica UF=%s year=%d: R$ %s",
        uf_upper, year, result["cesta_basica_capital_brl"],
    )
    return result


def build_cesta_basica_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build a DataFrame with cesta básica reference for all municipalities.

    Capital price is propagated to all municipalities in the UF —
    DIEESE only publishes at capital level.
    """
    cesta = fetch_cesta_basica_uf(uf, year)

    if cesta.get("cesta_basica_capital_brl") is None:
        logger.warning("No DIEESE data for UF=%s year=%d", uf, year)
        return pd.DataFrame()

    rows = []
    for cd in municipios_ibge:
        row = {
            "cd_municipio_ibge": cd,
            "sg_uf": uf.upper(),
            "data_referencia": f"{year}-12-01",
            "cesta_basica_capital_brl": cesta["cesta_basica_capital_brl"],
            "variacao_cesta_mensal_pct": cesta["variacao_cesta_mensal_pct"],
            "horas_trabalho_cesta": cesta["horas_trabalho_cesta"],
            "fontes": "DIEESE Cesta Básica Nacional (via IPEADATA)",
        }
        rows.append(row)

    return pd.DataFrame(rows)
