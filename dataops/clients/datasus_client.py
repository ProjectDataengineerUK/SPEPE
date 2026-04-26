"""DataSUS client — SIM (mortality) + SINASC (births) + ANS (health coverage)."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.datasus")

# DataSUS open data portal — SIM and SINASC bulk CSVs via SGDIF transfer
_DATASUS_FTP_BASE = "https://datasus.saude.gov.br/transferencia-de-arquivos/"

# TABNET / TABWIN public CSV endpoints (via custom REST wrapper used by OpenDataSUS libs)
_OPENDATASUS_BASE = "https://servicodados.datasus.gov.br"

# Fallback: IPEADATA has mortality indicators at state level
_IPEADATA_MORTALITY = (
    "http://www.ipeadata.gov.br/api/odata4/ValoresSerie"
    "(SERCODIGO='{serie}')?$top=500&$filter=NIVNOME%20eq%20'Municípios'"
)

_MORTALITY_SERIES = {
    "taxa_mortalidade_infantil_1000": "SAÚDE_TMORTALINF",
    "taxa_mortalidade_materna_100k": "SAÚDE_TMORTALMAT",
}

# ANS — Agência Nacional de Saúde Suplementar open data
_ANS_BENEFICIARIOS_URL = "https://dadosabertos.ans.gov.br/FTP/PDA/beneficiarios_por_municipio/"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get_json(url: str) -> dict:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get_csv(url: str, **kwargs) -> pd.DataFrame:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return pd.read_csv(BytesIO(resp.content), **kwargs)


def fetch_ipeadata_mortality(
    uf: str,
    year: int,
    municipios_ibge: list[int],
) -> dict[int, dict[str, float]]:
    """Fetch SIM mortality indicators from IPEADATA at municipal level.

    Returns {cd_municipio_ibge: {indicator_name: value}}.
    """
    result: dict[int, dict[str, float]] = {m: {} for m in municipios_ibge}
    ibge_set = {str(m) for m in municipios_ibge}

    for col_name, serie in _MORTALITY_SERIES.items():
        url = _IPEADATA_MORTALITY.format(serie=serie)
        try:
            data = _get_json(url)
        except Exception as exc:
            logger.warning("IPEADATA %s failed: %s", serie, exc)
            continue

        for item in data.get("value", []):
            tercodigo = str(item.get("TERCODIGO", ""))
            if tercodigo not in ibge_set:
                continue
            val_data = item.get("VALDATA", "")
            if str(year) not in str(val_data):
                continue
            try:
                val = float(item["VALVALOR"])
            except (TypeError, ValueError, KeyError):
                continue
            cd = int(tercodigo)
            result.setdefault(cd, {})[col_name] = val

    return result


def fetch_ans_cobertura(
    uf: str,
    year: int,
    municipios_ibge: list[int],
    pop_by_ibge: dict[int, int],
    cache_dir: Path,
) -> dict[int, float]:
    """Return {cd_municipio_ibge: pct_cobertura_plano_saude} from ANS open data."""
    cache_file = cache_dir / f"ans_beneficiarios_{uf.upper()}_{year}.parquet"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        # ANS publishes CSV files named by competência (YYYYMM)
        comp = f"{year}12"  # December snapshot as annual reference
        csv_url = f"{_ANS_BENEFICIARIOS_URL}{comp}.csv"
        try:
            df = _get_csv(
                csv_url,
                sep=";",
                encoding="latin-1",
                usecols=["CD_MUNICIPIO", "QT_BENEFICIARIO_ATIVO"],
                dtype={"CD_MUNICIPIO": str},
            )
            df.to_parquet(cache_file, index=False)
        except Exception as exc:
            logger.warning("ANS CSV fetch failed for UF=%s year=%d: %s", uf, year, exc)
            return {}

    ibge_set = {str(m) for m in municipios_ibge}
    result: dict[int, float] = {}

    for _, row in df.iterrows():
        cd_str = str(row.get("CD_MUNICIPIO", "")).strip()
        if cd_str not in ibge_set:
            continue
        cd = int(cd_str)
        beneficiarios = row.get("QT_BENEFICIARIO_ATIVO", 0) or 0
        pop = pop_by_ibge.get(cd, 0)
        if pop > 0:
            result[cd] = round(float(beneficiarios) / float(pop) * 100, 2)

    return result


def build_saude_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
    pop_by_ibge: dict[int, int],
    cache_dir: Path,
) -> pd.DataFrame:
    """Build the health indicators DataFrame for a UF × year."""
    mortality = fetch_ipeadata_mortality(uf, year, municipios_ibge)
    ans = fetch_ans_cobertura(uf, year, municipios_ibge, pop_by_ibge, cache_dir)

    rows = []
    for cd in municipios_ibge:
        row: dict = {
            "cd_municipio_ibge": cd,
            "sg_uf": uf.upper(),
            "ano": year,
        }
        row.update(mortality.get(cd, {}))
        if cd in ans:
            row["pct_cobertura_plano_saude"] = ans[cd]
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    fontes = ["DataSUS SIM (IPEADATA)"]
    if ans:
        fontes.append("ANS beneficiários")
    df["fontes"] = " + ".join(fontes)
    return df
