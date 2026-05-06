"""CadÚnico + Bolsa Família — dados agregados por município.

Fontes:
  A) Portal da Transparência API — beneficiários Bolsa Família por município (dezembro do ano)
     Requer: TRANSPARENCIA_API_KEY (cadastro gratuito em portaldatransparencia.gov.br/api-de-dados/cadastrar)
  B) MI Social / SAGI CSV — famílias CadÚnico por município e faixa de renda (sem autenticação)

Referência: https://api.portaldatransparencia.gov.br/swagger-ui.html
"""

from __future__ import annotations

import io
import logging
import os

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.cadunico")

_TRANSPARENCIA_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
_SAGI_BASE = "https://aplicacoes.mds.gov.br/sagi/vis/data3"

_PAGE_SIZE = 500


def _transparencia_headers() -> dict[str, str]:
    api_key = os.environ.get("TRANSPARENCIA_API_KEY", "")
    if not api_key:
        logger.warning(
            "TRANSPARENCIA_API_KEY não configurada — Portal da Transparência retornará 401. "
            "Cadastre em portaldatransparencia.gov.br/api-de-dados/cadastrar"
        )
    return {"chave-api-dados": api_key} if api_key else {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _get_transparencia(url: str, params: dict) -> list[dict]:
    resp = requests.get(url, params=params, headers=_transparencia_headers(), timeout=60)
    resp.raise_for_status()
    return resp.json()


def _fetch_bolsa_familia_municipio(year: int) -> pd.DataFrame:
    """Fetch Bolsa Família beneficiaries by municipality (December snapshot)."""
    mes_ano = f"{year}12"
    url = f"{_TRANSPARENCIA_BASE}/bolsa-familia-por-municipio"
    rows: list[dict] = []
    pagina = 1

    while True:
        try:
            data = _get_transparencia(url, {"mesAno": mes_ano, "pagina": pagina})
        except Exception as exc:
            logger.warning("Portal Transparência BF erro pág %d: %s", pagina, exc)
            break

        if not data:
            break

        for item in data:
            mun = item.get("municipio", {})
            rows.append(
                {
                    "cd_municipio_ibge": str(mun.get("codigoIBGE", ""))[:7],
                    "nm_municipio": mun.get("nomeIBGE", ""),
                    "sg_uf": mun.get("uf", {}).get("sigla", "")
                    if isinstance(mun.get("uf"), dict)
                    else "",
                    "ano": year,
                    "mes": int(str(mes_ano)[4:6]),
                    "qtd_beneficiarios_bolsa_familia": item.get("quantidadeBeneficiados", 0),
                    "valor_total_bolsa_familia_reais": float(item.get("valor", 0) or 0),
                    "fonte": "portal_transparencia",
                }
            )

        if len(data) < _PAGE_SIZE:
            break
        pagina += 1

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["cd_municipio_ibge"] = df["cd_municipio_ibge"].str[:7]
    logger.info("Bolsa Família %d: %d municípios", year, len(df))
    return df


def _fetch_cadunico_sagi(year: int) -> pd.DataFrame:
    """Fetch CadÚnico families by municipality from SAGI/MI Social CSV."""
    # MI Social disponibiliza CSVs anuais de famílias CadÚnico por município
    # URL base: https://aplicacoes.mds.gov.br/sagi/vis/data3/v.php
    url = f"{_SAGI_BASE}/v.php"
    params = {
        "q[]": "getdata",
        "t[]": "cadunico_municipio",
        "filtros[]": f"ano_ref={year}",
        "campos[]": "id_municipio,qtd_familias_cadunico,qtd_familias_pobreza,qtd_familias_extrema_pobreza",
        "formato": "csv",
    }
    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), sep=";", dtype=str)
        if df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "id_municipio": "cd_municipio_ibge",
                "qtd_familias_cadunico": "qtd_familias_cadunico",
                "qtd_familias_pobreza": "qtd_familias_baixa_renda",
                "qtd_familias_extrema_pobreza": "qtd_familias_extrema_pobreza",
            }
        )
        df["ano"] = year
        df["fonte"] = "sagi_mds"
        for col in [
            "qtd_familias_cadunico",
            "qtd_familias_baixa_renda",
            "qtd_familias_extrema_pobreza",
        ]:
            if col in df.columns:
                df[col] = (
                    pd.to_numeric(df[col].str.replace(",", "."), errors="coerce")
                    .fillna(0)
                    .astype(int)
                )

        logger.info("SAGI CadÚnico %d: %d municípios", year, len(df))
        return df
    except Exception as exc:
        logger.warning("SAGI CadÚnico %d falhou: %s", year, exc)
        return pd.DataFrame()


def _fetch_cadunico_dados_gov(year: int) -> pd.DataFrame:
    """Fetch CadÚnico aggregate data from dados.gov.br CKAN API."""
    # Dataset: Famílias no CadÚnico por município
    # Resource IDs variam por ano — tentativa com busca dinâmica
    search_url = "https://dados.gov.br/api/3/action/package_search"
    try:
        resp = requests.get(
            search_url,
            params={"q": "cadastro unico municipio", "rows": "5"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("result", {}).get("results", [])

        for pkg in results:
            for resource in pkg.get("resources", []):
                if (
                    str(year) in resource.get("name", "")
                    and resource.get("format", "").upper() == "CSV"
                ):
                    csv_url = resource["url"]
                    pd.read_csv(csv_url, sep=";", encoding="latin1", dtype=str, nrows=10)
                    logger.info("dados.gov.br CadÚnico %d: encontrado %s", year, csv_url)
                    # Schema varies — return empty and rely on other sources
                    return pd.DataFrame()
    except Exception as exc:
        logger.warning("dados.gov.br CadÚnico %d falhou: %s", year, exc)

    return pd.DataFrame()


def fetch_cadunico_municipio(year: int) -> pd.DataFrame:
    """Fetch consolidated CadÚnico + Bolsa Família data by municipality for a year.

    Returns DataFrame with one row per município with social program indicators.
    """
    df_bf = _fetch_bolsa_familia_municipio(year)
    df_sagi = _fetch_cadunico_sagi(year)

    if df_bf.empty and df_sagi.empty:
        logger.warning("Sem dados CadÚnico/BF para ano=%d", year)
        return pd.DataFrame()

    if df_bf.empty:
        df_sagi["qtd_beneficiarios_bolsa_familia"] = None
        df_sagi["valor_total_bolsa_familia_reais"] = None
        return df_sagi

    if df_sagi.empty:
        df_bf["qtd_familias_cadunico"] = None
        df_bf["qtd_familias_extrema_pobreza"] = None
        df_bf["qtd_familias_baixa_renda"] = None
        return df_bf

    # Merge on cd_municipio_ibge (7-digit IBGE code)
    df_bf["cd_municipio_ibge"] = df_bf["cd_municipio_ibge"].str[:7]
    df_sagi["cd_municipio_ibge"] = df_sagi["cd_municipio_ibge"].str[:7]

    df = df_bf.merge(
        df_sagi[
            [
                "cd_municipio_ibge",
                "qtd_familias_cadunico",
                "qtd_familias_extrema_pobreza",
                "qtd_familias_baixa_renda",
            ]
        ],
        on="cd_municipio_ibge",
        how="left",
    )
    df["fonte"] = "portal_transparencia+sagi_mds"
    logger.info("CadÚnico merged %d: %d municípios", year, len(df))
    return df
