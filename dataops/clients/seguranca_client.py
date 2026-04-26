"""Segurança pública client — Atlas da Violência (IPEA) + SINESP + IVS."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.seguranca")

# IPEADATA OData endpoint
_IPEADATA_BASE = "http://www.ipeadata.gov.br/api/odata4"

# IVS (Índice de Vulnerabilidade Social) — IPEA, nível municipal, 2010+
_IVS_SERIES = {
    "ivs_total": "IVS_IVSTOTAL",
    "ivs_infraestrutura": "IVS_IVSINF",
    "ivs_capital_humano": "IVS_IVSCH",
    "ivs_renda_trabalho": "IVS_IVSRT",
}

# SINESP ocorrências — dados.gov.br (CSV nacional por tipo)
# Atualizado anualmente pela SENASP/MJ
_SINESP_CSV = (
    "https://dados.gov.br/dados/conjuntos-dados/"
    "sistema-nacional-de-informacoes-de-seguranca-publica"
)
_SINESP_DIRECT = (
    "https://www.gov.br/mj/pt-br/assuntos/sua-seguranca/seguranca-publica/"
    "estatistica/dados-nacionais-1/sinesp-api/ocorrencias/csv/{year}/csv"
)

# Atlas da Violência — IPEA, taxa de homicídios por 100k (nível municipal)
_ATLAS_CSV = (
    "https://www.ipea.gov.br/atlasviolencia/arquivos/indicadores/taxa-homicidios-municipios.csv"
)

# Macro-regiões por UF
_UF_REGIAO: dict[str, str] = {
    "AC": "N",
    "AM": "N",
    "AP": "N",
    "PA": "N",
    "RO": "N",
    "RR": "N",
    "TO": "N",
    "AL": "NE",
    "BA": "NE",
    "CE": "NE",
    "MA": "NE",
    "PB": "NE",
    "PE": "NE",
    "PI": "NE",
    "RN": "NE",
    "SE": "NE",
    "DF": "CO",
    "GO": "CO",
    "MS": "CO",
    "MT": "CO",
    "ES": "SE",
    "MG": "SE",
    "RJ": "SE",
    "SP": "SE",
    "PR": "S",
    "RS": "S",
    "SC": "S",
}

_REGIAO_NOME: dict[str, str] = {
    "N": "Norte",
    "NE": "Nordeste",
    "CO": "Centro-Oeste",
    "SE": "Sudeste",
    "S": "Sul",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get(url: str, **kwargs: Any) -> requests.Response:
    resp = requests.get(url, timeout=45, **kwargs)
    resp.raise_for_status()
    return resp


def _get_json(url: str, **kwargs: Any) -> Any:
    return _get(url, **kwargs).json()


# ---------------------------------------------------------------------------
# IVS — Índice de Vulnerabilidade Social (IPEA, nível municipal)
# ---------------------------------------------------------------------------


def _fetch_ivs_serie(serie_code: str) -> dict[str, float]:
    """Return {cd_municipio_ibge_str: float} for a given IVS series."""
    url = f"{_IPEADATA_BASE}/ValoresSerie(SERCODIGO='{serie_code}')"
    try:
        data = _get_json(url)
    except Exception as exc:
        logger.warning("IVS série %s falhou: %s", serie_code, exc)
        return {}

    result: dict[str, float] = {}
    for item in data.get("value", []):
        loc = str(item.get("TERCODIGO", ""))
        val = item.get("VALVALOR")
        if loc and val is not None:
            try:
                result[loc] = float(val)
            except (ValueError, TypeError):
                pass
    return result


def fetch_ivs(uf: str, municipios_ibge: list[int]) -> list[dict]:
    """Fetch all IVS dimensions for municipalities in a UF."""
    ibge_strs = {str(cd) for cd in municipios_ibge}
    sg_regiao = _UF_REGIAO.get(uf.upper(), "")
    nm_regiao = _REGIAO_NOME.get(sg_regiao, "")

    all_series: dict[str, dict[str, float]] = {}
    for name, code in _IVS_SERIES.items():
        logger.info("Buscando IVS série %s (%s) para UF=%s", code, name, uf)
        all_series[name] = _fetch_ivs_serie(code)

    rows: list[dict] = []
    for ibge_str in ibge_strs:
        row: dict = {
            "cd_municipio_ibge": int(ibge_str),
            "sg_uf": uf.upper(),
            "sg_regiao": sg_regiao,
            "nm_regiao": nm_regiao,
        }
        for name in _IVS_SERIES:
            row[name] = all_series[name].get(ibge_str)
        rows.append(row)

    logger.info("IVS: %d municípios carregados para UF=%s", len(rows), uf)
    return rows


# ---------------------------------------------------------------------------
# Atlas da Violência — taxa de homicídios por 100k (IPEA/FBSP)
# ---------------------------------------------------------------------------


def fetch_atlas_homicidios(uf: str, year: int, cache_dir: Path) -> list[dict]:
    """Download Atlas da Violência homicide rates, filter by UF and year."""
    cache_file = cache_dir / "atlas_violencia_municipios.csv"

    if not cache_file.exists():
        logger.info("Baixando Atlas da Violência (CSV nacional)…")
        try:
            resp = _get(_ATLAS_CSV)
            cache_file.write_bytes(resp.content)
            logger.info("Atlas da Violência salvo: %s (%d bytes)", cache_file, len(resp.content))
        except Exception as exc:
            logger.warning("Atlas da Violência indisponível: %s — usando fallback vazio", exc)
            return []

    try:
        df = pd.read_csv(cache_file, encoding="utf-8-sig", dtype=str)
    except Exception as exc:
        logger.error("Falha ao ler Atlas da Violência CSV: %s", exc)
        return []

    # Normaliza colunas (o CSV do IPEA usa nomes variados entre versões)
    col_map = {}
    for col in df.columns:
        low = col.lower().strip()
        if "ibge" in low or "cod_mun" in low or "cod_municipio" in low:
            col_map[col] = "cd_municipio_ibge"
        elif "uf" in low and "nome" not in low:
            col_map[col] = "sg_uf"
        elif "ano" in low or "year" in low:
            col_map[col] = "ano"
        elif "taxa" in low and "homicid" in low:
            col_map[col] = "taxa_homicidio_100k"
        elif "total" in low and "homicid" in low:
            col_map[col] = "qt_homicidios"
    df = df.rename(columns=col_map)

    required = {"cd_municipio_ibge", "sg_uf", "ano", "taxa_homicidio_100k"}
    if not required.issubset(df.columns):
        logger.warning(
            "Atlas da Violência CSV — colunas esperadas ausentes. Encontradas: %s",
            list(df.columns),
        )
        return []

    df = df[df["sg_uf"].str.upper() == uf.upper()]
    df = df[df["ano"].astype(str) == str(year)]
    df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce")
    df["taxa_homicidio_100k"] = pd.to_numeric(df["taxa_homicidio_100k"], errors="coerce")
    if "qt_homicidios" in df.columns:
        df["qt_homicidios"] = pd.to_numeric(df["qt_homicidios"], errors="coerce")

    return df.to_dict("records")


# ---------------------------------------------------------------------------
# SINESP — ocorrências criminais estaduais/municipais
# ---------------------------------------------------------------------------


def fetch_sinesp_ocorrencias(uf: str, year: int, cache_dir: Path) -> list[dict]:
    """Download SINESP crime occurrence data. Returns empty list if unavailable."""
    cache_file = cache_dir / f"sinesp_{uf.upper()}_{year}.csv"

    if not cache_file.exists():
        url = _SINESP_DIRECT.format(year=year)
        logger.info("Baixando SINESP %s %d…", uf, year)
        try:
            resp = _get(url)
            cache_file.write_bytes(resp.content)
        except Exception as exc:
            logger.warning("SINESP %s/%d indisponível: %s", uf, year, exc)
            return []

    try:
        df = pd.read_csv(cache_file, encoding="utf-8-sig", dtype=str)
    except Exception as exc:
        logger.error("Falha ao ler SINESP CSV: %s", exc)
        return []

    # Filtra UF se possível
    for col in df.columns:
        if "uf" in col.lower() or "estado" in col.lower():
            df = df[df[col].str.upper() == uf.upper()]
            break

    return df.to_dict("records")


# ---------------------------------------------------------------------------
# Interface unificada
# ---------------------------------------------------------------------------


def build_seguranca_dataframe(
    uf: str,
    year: int,
    municipios_ibge: list[int],
    cache_dir: Path,
) -> pd.DataFrame:
    """Merge IVS + Atlas da Violência into a single security DataFrame per municipality."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    ivs_rows = fetch_ivs(uf, municipios_ibge)
    atlas_rows = fetch_atlas_homicidios(uf, year, cache_dir)

    df_ivs = pd.DataFrame(ivs_rows) if ivs_rows else pd.DataFrame()
    df_atlas = pd.DataFrame(atlas_rows) if atlas_rows else pd.DataFrame()

    if df_ivs.empty and df_atlas.empty:
        logger.warning("Nenhum dado de segurança obtido para UF=%s ano=%d", uf, year)
        return pd.DataFrame()

    if df_ivs.empty:
        df = df_atlas.copy()
    elif df_atlas.empty:
        df = df_ivs.copy()
    else:
        df_atlas["cd_municipio_ibge"] = pd.to_numeric(
            df_atlas["cd_municipio_ibge"], errors="coerce"
        )
        df_ivs["cd_municipio_ibge"] = pd.to_numeric(df_ivs["cd_municipio_ibge"], errors="coerce")
        df = df_ivs.merge(
            df_atlas[["cd_municipio_ibge", "taxa_homicidio_100k", "qt_homicidios"]],
            on="cd_municipio_ibge",
            how="left",
        )

    df["ano"] = year
    df["sg_uf"] = uf.upper()
    if "sg_regiao" not in df.columns:
        sg = _UF_REGIAO.get(uf.upper(), "")
        df["sg_regiao"] = sg
        df["nm_regiao"] = _REGIAO_NOME.get(sg, "")

    return df
