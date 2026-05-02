"""DataSUS client — SIM (mortality) + SINASC (births) + ANS (health coverage).

Primary path: PySUS FTP (DBC files) — real municipal-level data.
Fallback:     IPEADATA REST API — state-level proxies when PySUS unavailable.
"""

from __future__ import annotations

import logging
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.datasus")

_HAS_PYSUS = find_spec("pysus") is not None

# Fallback: IPEADATA has mortality indicators at municipal level
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


def _fetch_sim_pysus(uf: str, year: int, municipios_ibge: list[int]) -> dict[int, dict]:
    """Fetch SIM mortality data via PySUS FTP (DBC format). Returns {ibge_code: {indicators}}.

    PySUS downloads DBC files from DataSUS FTP and decodes them to pandas DataFrames.
    Aggregates to municipal level: total óbitos + taxa por 100k (using populacao proxy).
    """
    if not _HAS_PYSUS:
        return {}
    try:
        from pysus.ftp.databases.sim import SIM  # pysus >= 0.3.1

        sim = SIM().load()
        files = sim.get_files(UF=uf.upper(), year=year)
        if not files:
            logger.info("PySUS SIM: nenhum arquivo para UF=%s ano=%d", uf, year)
            return {}

        df = sim.read(files[0] if isinstance(files, list) else files)
        if df is None or df.empty:
            return {}

        ibge_col = next(
            (c for c in df.columns if "CODMUNRES" in c.upper() or "MUNRES" in c.upper()), None
        )
        if ibge_col is None:
            logger.warning("PySUS SIM: coluna de município não encontrada")
            return {}

        df[ibge_col] = pd.to_numeric(df[ibge_col], errors="coerce")
        ibge_set = set(municipios_ibge)
        df = df[df[ibge_col].isin(ibge_set)]

        result: dict[int, dict] = {}
        for cd, grp in df.groupby(ibge_col):
            result[int(cd)] = {"qt_obitos_total": len(grp)}

        logger.info("PySUS SIM: %d municípios carregados para UF=%s ano=%d", len(result), uf, year)
        return result
    except Exception as exc:
        logger.warning(
            "PySUS SIM falhou para UF=%s ano=%d: %s — usando fallback IPEADATA", uf, year, exc
        )
        return {}


def _fetch_sinasc_pysus(uf: str, year: int, municipios_ibge: list[int]) -> dict[int, dict]:
    """Fetch SINASC births data via PySUS FTP. Returns {ibge_code: {taxa_natalidade_1000}}."""
    if not _HAS_PYSUS:
        return {}
    try:
        from pysus.ftp.databases.sinasc import SINASC

        sinasc = SINASC().load()
        files = sinasc.get_files(UF=uf.upper(), year=year)
        if not files:
            return {}

        df = sinasc.read(files[0] if isinstance(files, list) else files)
        if df is None or df.empty:
            return {}

        mun_col = next(
            (c for c in df.columns if "CODMUNNASC" in c.upper() or "MUNNASC" in c.upper()), None
        )
        if mun_col is None:
            return {}

        df[mun_col] = pd.to_numeric(df[mun_col], errors="coerce")
        ibge_set = set(municipios_ibge)
        df = df[df[mun_col].isin(ibge_set)]

        result: dict[int, dict] = {}
        for cd, grp in df.groupby(mun_col):
            result[int(cd)] = {"qt_nascimentos": len(grp)}

        logger.info("PySUS SINASC: %d municípios para UF=%s ano=%d", len(result), uf, year)
        return result
    except Exception as exc:
        logger.warning("PySUS SINASC falhou para UF=%s ano=%d: %s", uf, year, exc)
        return {}


def build_saude_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
    pop_by_ibge: dict[int, int],
    cache_dir: Path,
) -> pd.DataFrame:
    """Build the health indicators DataFrame for a UF × year.

    Strategy:
    1. PySUS FTP → SIM (mortality counts) + SINASC (births)
    2. IPEADATA fallback for mortality rates when PySUS unavailable
    3. ANS open data for health plan coverage %
    """
    # ── Primary: PySUS FTP ───────────────────────────────────────────────────
    sim_data = _fetch_sim_pysus(uf, year, municipios_ibge)
    sinasc_data = _fetch_sinasc_pysus(uf, year, municipios_ibge)
    fontes: list[str] = []
    if sim_data:
        fontes.append("DataSUS SIM (PySUS FTP)")
    if sinasc_data:
        fontes.append("DataSUS SINASC (PySUS FTP)")

    # ── Fallback: IPEADATA for mortality rates ───────────────────────────────
    mortality_rates: dict[int, dict] = {}
    if not sim_data:
        mortality_rates = fetch_ipeadata_mortality(uf, year, municipios_ibge)
        if mortality_rates:
            fontes.append("DataSUS SIM (IPEADATA)")

    # ── ANS coverage ─────────────────────────────────────────────────────────
    ans = fetch_ans_cobertura(uf, year, municipios_ibge, pop_by_ibge, cache_dir)
    if ans:
        fontes.append("ANS beneficiários")

    rows = []
    for cd in municipios_ibge:
        row: dict = {"cd_municipio_ibge": cd, "sg_uf": uf.upper(), "ano": year}
        row.update(sim_data.get(cd, {}))
        row.update(sinasc_data.get(cd, {}))
        row.update(mortality_rates.get(cd, {}))
        if cd in ans:
            row["pct_cobertura_plano_saude"] = ans[cd]
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["fontes"] = " + ".join(fontes) if fontes else "sem dados"
    return df
