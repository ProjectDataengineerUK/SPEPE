"""Banco Central do Brasil client — endividamento e inadimplência das famílias.

Source: SGS (Sistema Gerenciador de Séries Temporais) — API pública, sem auth.
API: https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados
"""

from __future__ import annotations

import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.bacen")

_BACEN_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"

# SGS series — national level, monthly frequency
_SERIES: dict[str, tuple[int, str]] = {
    # (codigo_sgs, description)
    "endividamento_familias_pct": (29037, "Endividamento das famílias com SFN (% renda 12m)"),
    "comprometimento_renda_pct": (29038, "Comprometimento de renda das famílias com SFN (%)"),
    "inadimplencia_pf_pct": (21084, "Inadimplência PF — carteira total (%)"),
    "inadimplencia_pf_credito": (20400, "Inadimplência PF — crédito ampliado (%)"),
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_series(codigo: int, data_inicial: str, data_final: str) -> list[dict]:
    url = _BACEN_SGS.format(codigo=codigo)
    params = {
        "formato": "json",
        "dataInicial": data_inicial,
        "dataFinal": data_final,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_endividamento(year_start: int, year_end: int) -> pd.DataFrame:
    """Fetch monthly endividamento series from Banco Central SGS.

    Returns DataFrame with columns: data_referencia, <series_columns>
    Covers year_start–year_end inclusive, monthly granularity.
    """
    data_inicial = f"01/01/{year_start}"
    data_final = f"31/12/{year_end}"

    frames: dict[str, pd.Series] = {}
    for col_name, (codigo, desc) in _SERIES.items():
        try:
            records = _fetch_series(codigo, data_inicial, data_final)
            if not records:
                logger.warning(
                    "BCB SGS %d (%s): sem dados para %d-%d", codigo, col_name, year_start, year_end
                )
                continue
            s = pd.Series(
                {
                    r["data"]: float(r["valor"].replace(",", "."))
                    for r in records
                    if r.get("valor") not in (None, "", "-")
                },
                name=col_name,
            )
            frames[col_name] = s
            logger.info("BCB SGS %d: %d observações (%d–%d)", codigo, len(s), year_start, year_end)
        except Exception as exc:
            logger.warning("BCB SGS %d falhou: %s", codigo, exc)

    if not frames:
        return pd.DataFrame()

    df = pd.DataFrame(frames)
    df.index.name = "data_referencia_bcb"
    df = df.reset_index()

    # Parse Brazilian date format DD/MM/YYYY → date
    df["data_referencia"] = pd.to_datetime(
        df["data_referencia_bcb"], format="%d/%m/%Y", errors="coerce"
    )
    df = df.dropna(subset=["data_referencia"])
    df["ano"] = df["data_referencia"].dt.year
    df["mes"] = df["data_referencia"].dt.month
    df["data_referencia"] = df["data_referencia"].dt.strftime("%Y-%m-%d")
    df = df.drop(columns=["data_referencia_bcb"])
    df["fontes"] = "Banco Central do Brasil — SGS"
    df["granularidade"] = "Nacional"

    return df


def build_endividamento_dataframe(
    uf: str,
    year_start: int,
    year_end: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build municipal DataFrame with endividamento indicators.

    BCB publishes national-level data only.
    Values are propagated to all municipalities in the UF.
    """
    df_nacional = fetch_endividamento(year_start, year_end)
    if df_nacional.empty:
        logger.warning("Sem dados BCB endividamento para %d-%d", year_start, year_end)
        return pd.DataFrame()

    rows = []
    for _, row in df_nacional.iterrows():
        for cd in municipios_ibge:
            r = row.to_dict()
            r["cd_municipio_ibge"] = cd
            r["sg_uf"] = uf.upper()
            rows.append(r)

    result = pd.DataFrame(rows)
    logger.info(
        "BCB endividamento UF=%s: %d registros municipais × %d períodos",
        uf.upper(),
        len(result),
        len(df_nacional),
    )
    return result
