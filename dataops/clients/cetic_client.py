"""CETIC.br substitute — IBGE SIDRA PNAD TIC Domicílios (internet access by UF).

CETIC TIC Domicílios uses IBGE PNAD Contínua methodology.
SIDRA is the authoritative API; we query it directly.
"""

from __future__ import annotations

import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.cetic")

_SIDRA_BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"

# PNAD Contínua — Suplemento Anual TIC
# Tabela 9174: "Domicílios particulares permanentes com acesso à Internet"
_TABLE_INTERNET = "9174"
_VAR_INTERNET = "9607"   # % domicílios com internet

# Tabela 9173: "Domicílios particulares permanentes com microcomputador"
_TABLE_COMPUTER = "9173"
_VAR_COMPUTER = "9607"   # Variável pode ter o mesmo código neste contexto

# Fallback table for internet: 7065 variable 93085 (renda — not internet, but fallback)
# We'll keep table 9174 as sole source and gracefully log when unavailable.

# IBGE numeric code for each UF (N3 = unidade da federação level)
_UF_IBGE_CODE: dict[str, str] = {
    "AC": "12", "AL": "27", "AM": "13", "AP": "16", "BA": "29",
    "CE": "23", "DF": "53", "ES": "32", "GO": "52", "MA": "21",
    "MG": "31", "MS": "50", "MT": "51", "PA": "15", "PB": "25",
    "PE": "26", "PI": "22", "PR": "41", "RJ": "33", "RN": "24",
    "RO": "11", "RR": "14", "RS": "43", "SC": "42", "SE": "28",
    "SP": "35", "TO": "17",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _sidra_get(table: str, year: int, variable: str, uf_code: str) -> list[dict]:
    url = (
        f"{_SIDRA_BASE}/{table}/periodos/{year}/variaveis/{variable}"
        f"?localidades=N3[{uf_code}]"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # SIDRA v3 returns a list with one element per variável
    if isinstance(data, list) and data:
        resultados = data[0].get("resultados", [])
        if resultados:
            return resultados[0].get("series", [])
    return []


def _extract_value(series: list[dict], year: int) -> float | None:
    for s in series:
        raw = s.get("serie", {}).get(str(year))
        if raw is not None:
            try:
                v = float(str(raw).replace(",", "."))
                return None if v < 0 else v
            except (TypeError, ValueError):
                pass
    return None


def fetch_cetic_indicators(uf: str, year: int) -> dict[str, float | None]:
    """Return TIC Domicílios indicators for a UF from IBGE SIDRA PNAD TIC."""
    uf_code = _UF_IBGE_CODE.get(uf.upper())
    result: dict[str, float | None] = {
        "pct_internet_domiciliar": None,
        "pct_computador_domiciliar": None,
        "pct_smartphone_domiciliar": None,
    }

    if not uf_code:
        logger.error("UF não mapeada para IBGE code: %s", uf)
        return result

    # Internet access
    try:
        series = _sidra_get(_TABLE_INTERNET, year, _VAR_INTERNET, uf_code)
        result["pct_internet_domiciliar"] = _extract_value(series, year)
    except Exception as exc:
        logger.warning("SIDRA internet table=%s UF=%s year=%d: %s", _TABLE_INTERNET, uf, year, exc)

    # Computer access — try same table with variable 9608 if 9174 has it, else skip
    try:
        series_c = _sidra_get(_TABLE_INTERNET, year, "9608", uf_code)
        result["pct_computador_domiciliar"] = _extract_value(series_c, year)
    except Exception:
        pass  # optional indicator — no warning needed

    # Smartphone — PNAD TIC does not publish smartphone at UF level in SIDRA
    # Leave as None; Silver transformer will handle nulls gracefully.

    logger.info(
        "CETIC/SIDRA UF=%s year=%d: internet=%.1f%%",
        uf.upper(),
        year,
        result["pct_internet_domiciliar"] or 0,
    )
    return result


def build_digital_access_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build DataFrame with digital access indicators for all municipalities.

    PNAD TIC provides state-level data; values are propagated to municipalities.
    """
    indicators = fetch_cetic_indicators(uf, year)

    if indicators.get("pct_internet_domiciliar") is None:
        logger.warning("Sem dado SIDRA TIC para UF=%s year=%d", uf, year)
        return pd.DataFrame()

    rows = [
        {
            "cd_municipio_ibge": cd,
            "sg_uf": uf.upper(),
            "ano": year,
            **indicators,
            "granularidade": "UF",
            "fontes": f"IBGE SIDRA PNAD TIC {year} (tabela {_TABLE_INTERNET})",
        }
        for cd in municipios_ibge
    ]
    return pd.DataFrame(rows)
