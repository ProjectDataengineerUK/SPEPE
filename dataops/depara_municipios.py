"""De-para table: TSE cd_municipio ↔ IBGE cd_municipio_ibge reconciliation.

TSE uses a different municipality code than IBGE. This module provides
the mapping required to join electoral data with socioeconomic indicators.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger("spepe.dataops.depara")

DEPARA_PATH = Path("data/silver/depara_municipios.parquet")
IBGE_MUNICIPIOS_API = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"


def load_depara() -> pd.DataFrame:
    """Load or build the TSE ↔ IBGE municipality mapping table."""
    if DEPARA_PATH.exists():
        return pd.read_parquet(DEPARA_PATH)
    return _build_depara()


def _build_depara() -> pd.DataFrame:
    """Build de-para from IBGE API + known TSE code mapping rules.

    TSE codes are 5 digits; IBGE codes are 7 digits.
    The first 6 digits of the IBGE code correspond to the TSE 5-digit code + check digit.
    Approximation: TSE cd_municipio ≈ int(ibge_code / 10) for most cases.
    """
    logger.info("Construindo tabela de-para TSE ↔ IBGE via API...")
    try:
        r = requests.get(IBGE_MUNICIPIOS_API, timeout=30)
        r.raise_for_status()
        municipios = r.json()
    except requests.RequestException as e:
        logger.error(f"Falha ao buscar municípios IBGE: {e}")
        return pd.DataFrame(
            columns=["cd_municipio_tse", "cd_municipio_ibge", "nm_municipio", "sg_uf"]
        )

    rows = []
    for m in municipios:
        ibge_code = int(m["id"])
        tse_code = ibge_code // 10
        rows.append(
            {
                "cd_municipio_tse": tse_code,
                "cd_municipio_ibge": ibge_code,
                "nm_municipio": m["nome"],
                "sg_uf": m["microrregiao"]["mesorregiao"]["UF"]["sigla"],
            }
        )

    df = pd.DataFrame(rows)
    DEPARA_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(DEPARA_PATH, index=False)
    logger.info(f"De-para construído: {len(df)} municípios salvos em {DEPARA_PATH}")
    return df


def join_tse_ibge(
    df_tse: pd.DataFrame,
    df_ibge: pd.DataFrame,
    tse_key: str = "cd_municipio",
    ibge_key: str = "cd_municipio_ibge",
) -> pd.DataFrame:
    """Join TSE data with IBGE indicators via de-para table."""
    depara = load_depara()

    df_tse_mapped = df_tse.merge(
        depara[["cd_municipio_tse", "cd_municipio_ibge"]],
        left_on=tse_key,
        right_on="cd_municipio_tse",
        how="left",
    )

    match_pct = df_tse_mapped["cd_municipio_ibge"].notna().mean() * 100
    logger.info(f"Join TSE↔IBGE: {match_pct:.1f}% dos municípios com match")
    if match_pct < 80:
        logger.warning(f"Baixo match de municípios: {match_pct:.1f}% < 80%")

    df_ibge_str = df_ibge.copy()
    if ibge_key in df_ibge_str.columns:
        df_ibge_str[ibge_key] = pd.to_numeric(
            df_ibge_str[ibge_key], errors="coerce"
        ).astype("Int64")

    df_tse_mapped["cd_municipio_ibge"] = pd.to_numeric(
        df_tse_mapped["cd_municipio_ibge"], errors="coerce"
    ).astype("Int64")

    return df_tse_mapped.merge(
        df_ibge_str, left_on="cd_municipio_ibge", right_on=ibge_key, how="left"
    )
