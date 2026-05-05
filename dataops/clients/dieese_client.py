"""DIEESE substitute — IBGE IPCA 'alimentação no domicílio' por região metropolitana.

DIEESE só disponibiliza API para São Paulo no IPEADATA. Para os demais estados,
usamos o IPCA 'alimentação no domicílio' por região metropolitana como proxy.
Estados sem RM IPCA usam o dado nacional.

Source: IBGE SIDRA tabela 1419 via apisidra.ibge.gov.br (público, sem autenticação).
"""

from __future__ import annotations

import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.dieese")

_APISIDRA_BASE = "https://apisidra.ibge.gov.br/values"

# Tabela 7060: IPCA variações por grupo e região metropolitana (a partir de jan/2020)
# Variável 63: variação mensal (%); variável 2265: variação acumulada 12 meses (%)
# Classificação 315 / categoria 7171: "11.Alimentação no domicílio"
_TABLE_IPCA = "7060"
_VAR_MENSAL = "63"
_VAR_12M = "2265"
_CLASSIF_ALIMENTACAO = "315"
_CAT_ALIMENTACAO = "7171"

# Código da região metropolitana (D1C) → UF
_METRO_UF: dict[str, str] = {
    "1501": "PA",   # Belém
    "2301": "CE",   # Fortaleza
    "2601": "PE",   # Recife
    "2901": "BA",   # Salvador
    "3101": "MG",   # Belo Horizonte
    "3201": "ES",   # Grande Vitória
    "3301": "RJ",   # Rio de Janeiro
    "3501": "SP",   # São Paulo
    "4101": "PR",   # Curitiba
    "4301": "RS",   # Porto Alegre
}

_UFS_WITH_METRO = set(_METRO_UF.values())

_UF_CAPITAL_IBGE: dict[str, int] = {
    "AC": 1200401, "AL": 2704302, "AM": 1302603, "AP": 1600303,
    "BA": 2927408, "CE": 2304400, "DF": 5300108, "ES": 3205309,
    "GO": 5208707, "MA": 2111300, "MG": 3106200, "MS": 5002704,
    "MT": 5103403, "PA": 1501402, "PB": 2507507, "PE": 2611606,
    "PI": 2211001, "PR": 4106902, "RJ": 3304557, "RN": 2408102,
    "RO": 1100205, "RR": 1400100, "RS": 4314902, "SC": 4205407,
    "SE": 2800308, "SP": 3550308, "TO": 1721000,
}


def _months_for_year(year: int) -> str:
    return ",".join(f"{year}{m:02d}" for m in range(1, 13))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_ipca_metro(year: int) -> list[dict]:
    periods = _months_for_year(year)
    url = (
        f"{_APISIDRA_BASE}/t/{_TABLE_IPCA}/n7/all"
        f"/v/{_VAR_MENSAL},{_VAR_12M}/p/{periods}"
        f"/c{_CLASSIF_ALIMENTACAO}/{_CAT_ALIMENTACAO}"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data[1:] if data else []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_ipca_nacional(year: int) -> list[dict]:
    periods = _months_for_year(year)
    url = (
        f"{_APISIDRA_BASE}/t/{_TABLE_IPCA}/n1/1"
        f"/v/{_VAR_MENSAL},{_VAR_12M}/p/{periods}"
        f"/c{_CLASSIF_ALIMENTACAO}/{_CAT_ALIMENTACAO}"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data[1:] if data else []


def _parse_rows(rows: list[dict]) -> pd.DataFrame:
    records = []
    for r in rows:
        try:
            v = float(r["V"]) if r.get("V") not in (None, "", "...") else None
        except (TypeError, ValueError):
            v = None
        records.append({
            "metro_code": r.get("D1C", ""),
            "metro_name": r.get("D1N", ""),
            "var_code": r.get("D2C", ""),
            "period": r.get("D3C", ""),
            "value": v,
        })
    df = pd.DataFrame(records)
    return df[df["value"].notna()] if not df.empty else df


def fetch_ipca_alimentacao(year: int) -> pd.DataFrame:
    """Fetch IPCA alimentação no domicílio for all metro regions + national for a year.

    Returns tidy DataFrame with: sg_uf, periodo_yyyymm, data_referencia,
    ipca_alimentacao_mensal_pct, ipca_alimentacao_12m_pct, granularidade, fontes.
    """
    df_metro = pd.DataFrame()
    try:
        df_metro = _parse_rows(_fetch_ipca_metro(year))
        logger.info("IPCA metro: %d observações para %d", len(df_metro), year)
    except Exception as exc:
        logger.warning("IPCA metro fetch falhou: %s", exc)

    df_nac = pd.DataFrame()
    try:
        df_nac = _parse_rows(_fetch_ipca_nacional(year))
        logger.info("IPCA nacional: %d observações para %d", len(df_nac), year)
    except Exception as exc:
        logger.warning("IPCA nacional fetch falhou: %s", exc)

    if df_metro.empty and df_nac.empty:
        return pd.DataFrame()

    result_rows = []

    if not df_metro.empty:
        for metro_code, sg_uf in _METRO_UF.items():
            df_uf = df_metro[df_metro["metro_code"] == metro_code]
            if df_uf.empty:
                continue
            metro_name = df_uf["metro_name"].iloc[0]
            for period in sorted(df_uf["period"].unique()):
                df_p = df_uf[df_uf["period"] == period]
                mensal = df_p[df_p["var_code"] == _VAR_MENSAL]["value"].values
                acum12m = df_p[df_p["var_code"] == _VAR_12M]["value"].values
                result_rows.append({
                    "sg_uf": sg_uf,
                    "periodo_yyyymm": period,
                    "data_referencia": f"{period[:4]}-{period[4:6]}-01",
                    "ipca_alimentacao_mensal_pct": float(mensal[0]) if len(mensal) else None,
                    "ipca_alimentacao_12m_pct": float(acum12m[0]) if len(acum12m) else None,
                    "granularidade": "RM",
                    "fontes": f"IBGE IPCA tabela 1419 — Alimentação no domicílio ({metro_name})",
                })

    if not df_nac.empty:
        ufs_sem_metro = set(_UF_CAPITAL_IBGE.keys()) - _UFS_WITH_METRO
        for period in sorted(df_nac["period"].unique()):
            df_p = df_nac[df_nac["period"] == period]
            mensal = df_p[df_p["var_code"] == _VAR_MENSAL]["value"].values
            acum12m = df_p[df_p["var_code"] == _VAR_12M]["value"].values
            for sg_uf in ufs_sem_metro:
                result_rows.append({
                    "sg_uf": sg_uf,
                    "periodo_yyyymm": period,
                    "data_referencia": f"{period[:4]}-{period[4:6]}-01",
                    "ipca_alimentacao_mensal_pct": float(mensal[0]) if len(mensal) else None,
                    "ipca_alimentacao_12m_pct": float(acum12m[0]) if len(acum12m) else None,
                    "granularidade": "Nacional",
                    "fontes": "IBGE IPCA tabela 1419 — Alimentação no domicílio (Brasil)",
                })

    return pd.DataFrame(result_rows) if result_rows else pd.DataFrame()


def build_cesta_basica_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build DataFrame with IPCA alimentação indicators for all municipalities.

    Returns one row per municipality × month with IPCA alimentação no domicílio.
    """
    df_ipca = fetch_ipca_alimentacao(year)
    if df_ipca.empty:
        logger.warning("Sem dados IPCA alimentação para year=%d", year)
        return pd.DataFrame()

    df_uf = df_ipca[df_ipca["sg_uf"] == uf.upper()]
    if df_uf.empty:
        logger.warning("Sem dados IPCA alimentação para UF=%s year=%d", uf, year)
        return pd.DataFrame()

    rows = []
    for _, ipca_row in df_uf.iterrows():
        for cd in municipios_ibge:
            r = ipca_row.to_dict()
            r["cd_municipio_ibge"] = cd
            rows.append(r)

    result = pd.DataFrame(rows)
    logger.info(
        "IPCA alimentação UF=%s year=%d: %d registros (%d municípios × %d meses)",
        uf.upper(), year, len(result), len(municipios_ibge), len(df_uf),
    )
    return result
