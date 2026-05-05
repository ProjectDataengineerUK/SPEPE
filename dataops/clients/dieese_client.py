"""DIEESE client — Cesta Básica Nacional (preço mensal por capital de UF).

Primary source: DIEESE public CSV endpoint (national, all capitals).
Fallback: IPEADATA OData API with known series codes per UF capital.
"""

from __future__ import annotations

import logging
from io import StringIO

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.dieese")

# DIEESE publishes a monthly CSV with cesta básica values for ~18 capitals.
# coduf=0 returns all cities tracked by DIEESE.
_DIEESE_CSV_URL = (
    "https://www.dieese.org.br/analise/web/remuneracao/"
    "processamentoRemuneracaoRetornoCSV.do"
    "?tipo=1&coduf=0&codmun=0&ano={year}&mes=12"
)

# IPEADATA OData — fallback series by UF capital.
# DIEESE tracks ~18 capitals; remaining UFs use the national median.
_IPEADATA_BASE = "https://ipeadata.gov.br/api/odata4/ValoresSerie"
_IPEADATA_NATIONAL_SERIES = "DIEESE_CBSAL"  # salário mínimo necessário (proxy)

# Known IPEADATA series codes for cesta básica by UF capital
# Format: PRECOS12_CB{city}12  (monthly frequency = 12)
_UF_IPEADATA_SERIES: dict[str, str] = {
    "SP": "PRECOS12_CBSP12",
    "RJ": "PRECOS12_CBRJ12",
    "MG": "PRECOS12_CBBH12",
    "RS": "PRECOS12_CBPA12",
    "PR": "PRECOS12_CBCT12",
    "BA": "PRECOS12_CBSA12",
    "CE": "PRECOS12_CBFO12",
    "PE": "PRECOS12_CBRE12",
    "GO": "PRECOS12_CBGO12",
    "MS": "PRECOS12_CBCG12",
    "ES": "PRECOS12_CBVI12",
    "MA": "PRECOS12_CBSL12",
    "PA": "PRECOS12_CBBEL12",
    "SC": "PRECOS12_CBFL12",
    "AM": "PRECOS12_CBMA12",
    "PI": "PRECOS12_CBTE12",
    "SE": "PRECOS12_CBARA12",
    "RN": "PRECOS12_CBNAT12",
}

# Map UF → capital IBGE code
_UF_CAPITAL_IBGE: dict[str, int] = {
    "AC": 1200401, "AL": 2704302, "AM": 1302603, "AP": 1600303,
    "BA": 2927408, "CE": 2304400, "DF": 5300108, "ES": 3205309,
    "GO": 5208707, "MA": 2111300, "MG": 3106200, "MS": 5002704,
    "MT": 5103403, "PA": 1501402, "PB": 2507507, "PE": 2611606,
    "PI": 2211001, "PR": 4106902, "RJ": 3304557, "RN": 2408102,
    "RO": 1100205, "RR": 1400100, "RS": 4314902, "SC": 4205407,
    "SE": 2800308, "SP": 3550308, "TO": 1721000,
}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=5, max=20))
def _fetch_dieese_csv(year: int) -> pd.DataFrame:
    url = _DIEESE_CSV_URL.format(year=year)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    content = resp.content.decode("latin-1", errors="replace")
    # DIEESE CSVs use semicolons as separator
    df = pd.read_csv(StringIO(content), sep=";", decimal=",", thousands=".", encoding="latin-1")
    return df


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_ipeadata_series(series_code: str) -> list[dict]:
    url = f"{_IPEADATA_BASE}(SERCODIGO='{series_code}')?$top=24&$orderby=VALDATA desc"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json().get("value", [])


def _parse_dieese_csv_for_uf(df: pd.DataFrame, uf: str, year: int) -> float | None:
    """Extract cesta básica value for a UF from DIEESE national CSV."""
    uf_upper = uf.upper()
    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Look for UF column (various possible names)
    uf_col = next((c for c in df.columns if "uf" in c or "sigla" in c or "estado" in c), None)
    val_col = next(
        (c for c in df.columns if "cesta" in c or "valor" in c or "preco" in c or "vl" in c),
        None,
    )

    if not uf_col or not val_col:
        logger.debug("DIEESE CSV colunas não reconhecidas: %s", list(df.columns))
        return None

    row = df[df[uf_col].astype(str).str.upper().str.strip() == uf_upper]
    if row.empty:
        return None

    try:
        val = float(str(row.iloc[0][val_col]).replace(",", ".").replace("R$", "").strip())
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch_via_ipeadata(uf: str, year: int) -> float | None:
    series_code = _UF_IPEADATA_SERIES.get(uf.upper(), _IPEADATA_NATIONAL_SERIES)
    try:
        entries = _fetch_ipeadata_series(series_code)
    except Exception as exc:
        logger.warning("IPEADATA fetch failed (series=%s): %s", series_code, exc)
        return None

    year_entries = [e for e in entries if str(year) in str(e.get("VALDATA", ""))]
    vals = []
    for e in year_entries:
        try:
            v = float(e["VALVALOR"])
            if v > 0:
                vals.append(v)
        except (TypeError, ValueError, KeyError):
            pass

    return round(sum(vals) / len(vals), 2) if vals else None


def fetch_cesta_basica_uf(uf: str, year: int) -> dict[str, float | None]:
    """Return cesta básica indicators for a UF in a given year."""
    uf_upper = uf.upper()
    result: dict[str, float | None] = {
        "cesta_basica_capital_brl": None,
        "variacao_cesta_mensal_pct": None,
        "horas_trabalho_cesta": None,
    }

    # Primary: DIEESE direct CSV
    try:
        df_csv = _fetch_dieese_csv(year)
        value = _parse_dieese_csv_for_uf(df_csv, uf_upper, year)
        if value:
            result["cesta_basica_capital_brl"] = value
            logger.info("DIEESE CSV: UF=%s year=%d R$ %.2f", uf_upper, year, value)
            return result
    except Exception as exc:
        logger.warning("DIEESE CSV indisponível (UF=%s year=%d): %s", uf_upper, year, exc)

    # Fallback: IPEADATA per-capital series
    value = _fetch_via_ipeadata(uf_upper, year)
    if value:
        result["cesta_basica_capital_brl"] = value
        logger.info("IPEADATA DIEESE: UF=%s year=%d R$ %.2f", uf_upper, year, value)

    return result


def build_cesta_basica_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> pd.DataFrame:
    """Build DataFrame with cesta básica reference for all municipalities.

    Capital price is propagated to all municipalities in the UF —
    DIEESE only publishes at capital level.
    """
    cesta = fetch_cesta_basica_uf(uf, year)

    if cesta.get("cesta_basica_capital_brl") is None:
        logger.warning("Sem dado DIEESE para UF=%s year=%d", uf, year)
        return pd.DataFrame()

    rows = [
        {
            "cd_municipio_ibge": cd,
            "sg_uf": uf.upper(),
            "data_referencia": f"{year}-12-01",
            "cesta_basica_capital_brl": cesta["cesta_basica_capital_brl"],
            "variacao_cesta_mensal_pct": cesta["variacao_cesta_mensal_pct"],
            "horas_trabalho_cesta": cesta["horas_trabalho_cesta"],
            "fontes": "DIEESE Cesta Básica Nacional",
        }
        for cd in municipios_ibge
    ]
    return pd.DataFrame(rows)
