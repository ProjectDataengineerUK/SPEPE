"""CETIC.br client — TIC Domicílios digital access indicators."""

from __future__ import annotations

import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.cetic")

# CETIC.br TIC Domicílios — public API for digital inclusion indicators
# API docs: https://cetic.br/api/indicadores/
_CETIC_API_BASE = "https://cetic.br/api/indicadores/"

# TIC Domicílios key indicators (state-level granularity)
_CETIC_INDICATORS = {
    "pct_internet_domiciliar": {
        "pesquisa": "tic-domicilios",
        "indicador": "C1",          # % domicílios com acesso à internet
        "dimensao": "Total",
    },
    "pct_computador_domiciliar": {
        "pesquisa": "tic-domicilios",
        "indicador": "B7",          # % domicílios com computador
        "dimensao": "Total",
    },
    "pct_smartphone_domiciliar": {
        "pesquisa": "tic-domicilios",
        "indicador": "C2B",         # % domicílios com smartphone
        "dimensao": "Total",
    },
}

# IBGE PNAD TIC — fallback (state-level, only UF granularity available)
_PNAD_TIC_SIDRA = {
    "pct_internet_domiciliar": ("9606", "2023", "9607"),
}

_CETIC_UF_MAP: dict[str, str] = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
    "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso",
    "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí",
    "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RO": "Rondônia", "RR": "Roraima", "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo", "TO": "Tocantins",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get(url: str) -> dict:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_cetic_indicators(uf: str, year: int) -> dict[str, float | None]:
    """Return CETIC TIC Domicílios indicators for a UF in a given year.

    Returns dict keyed by indicator name (state-level — propagated to municipalities).
    """
    uf_upper = uf.upper()
    uf_name = _CETIC_UF_MAP.get(uf_upper, uf_upper)
    result: dict[str, float | None] = {k: None for k in _CETIC_INDICATORS}

    for col_name, cfg in _CETIC_INDICATORS.items():
        url = (
            f"{_CETIC_API_BASE}{cfg['pesquisa']}/indicadores/{cfg['indicador']}/"
            f"?ano={year}&regiao={uf_name}&dimensao={cfg['dimensao']}"
        )
        try:
            data = _get(url)
        except Exception as exc:
            logger.debug("CETIC %s %s failed: %s", col_name, uf, exc)
            continue

        # CETIC API returns list of {"regiao": ..., "ano": ..., "valor": ...}
        entries = data if isinstance(data, list) else data.get("data", [])
        for item in entries:
            if str(item.get("ano", "")) == str(year):
                try:
                    result[col_name] = float(str(item["valor"]).replace(",", "."))
                except (TypeError, ValueError, KeyError):
                    pass
                break

    logger.info(
        "CETIC TIC UF=%s year=%d: internet=%s%%",
        uf_upper, year, result["pct_internet_domiciliar"],
    )
    return result


def build_digital_access_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build DataFrame with digital access indicators for all municipalities.

    CETIC provides state-level data; values are propagated to all municipalities.
    Municipal-level breakdown not publicly available — UF value is the best proxy.
    """
    indicators = fetch_cetic_indicators(uf, year)

    # Require at least internet access to be non-null
    if indicators.get("pct_internet_domiciliar") is None:
        logger.warning("No CETIC digital data for UF=%s year=%d", uf, year)
        return pd.DataFrame()

    rows = []
    for cd in municipios_ibge:
        row = {
            "cd_municipio_ibge": cd,
            "sg_uf": uf.upper(),
            "ano": year,
            **indicators,
            "granularidade": "UF",  # state-level proxy
            "fontes": f"CETIC TIC Domicílios {year}",
        }
        rows.append(row)

    return pd.DataFrame(rows)
