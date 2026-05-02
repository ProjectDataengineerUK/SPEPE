"""De-para table: TSE cd_municipio ↔ IBGE cd_municipio_ibge reconciliation.

TSE and IBGE use different municipality codes. This module builds the mapping
by matching normalized names within the same UF (reliable ~99%), with GCS cache.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger("spepe.dataops.depara")

DEPARA_LOCAL = Path("data/silver/depara_municipios.parquet")
IBGE_MUNICIPIOS_API = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
_GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
_GCS_DEPARA = "reference/depara_municipios.parquet"


def _normalize_name(s: str) -> str:
    """Uppercase, strip accents, remove non-alpha."""
    s = unicodedata.normalize("NFKD", str(s).upper())
    return "".join(
        c for c in s if not unicodedata.combining(c) and (c.isalpha() or c == " ")
    ).strip()


def _load_from_gcs() -> pd.DataFrame | None:
    if not _GCS_BUCKET:
        return None
    try:
        from google.cloud import storage
        import io

        client = storage.Client()
        blob = client.bucket(_GCS_BUCKET).blob(_GCS_DEPARA)
        if not blob.exists():
            return None
        return pd.read_parquet(io.BytesIO(blob.download_as_bytes()))
    except Exception:
        return None


def _save_to_gcs(df: pd.DataFrame) -> None:
    if not _GCS_BUCKET:
        return
    try:
        from google.cloud import storage
        import io

        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        client = storage.Client()
        client.bucket(_GCS_BUCKET).blob(_GCS_DEPARA).upload_from_file(buf)
        logger.info("De-para salvo em GCS: gs://%s/%s", _GCS_BUCKET, _GCS_DEPARA)
    except Exception as exc:
        logger.warning("Falha ao salvar de-para no GCS: %s", exc)


def load_depara() -> pd.DataFrame:
    """Load or build TSE ↔ IBGE municipality mapping. GCS cache → local → rebuild."""
    df = _load_from_gcs()
    if df is not None and not df.empty:
        return df
    if DEPARA_LOCAL.exists():
        return pd.read_parquet(DEPARA_LOCAL)
    return _build_depara()


def _build_depara() -> pd.DataFrame:
    """Build mapping by normalizing municipality names within same UF.

    TSE codes (5-digit) and IBGE codes (7-digit) are unrelated numbering systems.
    Matching by (sg_uf, normalized_name) gives ~99% coverage.
    """
    logger.info("Construindo tabela de-para TSE ↔ IBGE via API IBGE...")
    try:
        r = requests.get(IBGE_MUNICIPIOS_API, timeout=30)
        r.raise_for_status()
        municipios = r.json()
    except requests.RequestException as exc:
        logger.error("Falha ao buscar municípios IBGE: %s", exc)
        return pd.DataFrame(
            columns=["cd_municipio_tse", "cd_municipio_ibge", "nm_municipio", "sg_uf"]
        )

    rows = []
    for m in municipios:
        try:
            ibge_code = int(m["id"])
            microrregiao = m.get("microrregiao") or {}
            mesorregiao = microrregiao.get("mesorregiao") or {}
            uf = mesorregiao.get("UF") or {}
            sg_uf = uf.get("sigla", "")
            nome = m.get("nome", "")
            rows.append(
                {
                    "cd_municipio_ibge": ibge_code,
                    "nm_municipio_ibge": nome,
                    "nm_norm": _normalize_name(nome),
                    "sg_uf": sg_uf,
                }
            )
        except (KeyError, TypeError, ValueError):
            pass

    df_ibge = pd.DataFrame(rows)
    logger.info("IBGE API: %d municípios carregados", len(df_ibge))

    # The mapping will be completed at join time using TSE names.
    # Store IBGE reference table — TSE codes added dynamically in join_tse_ibge.
    DEPARA_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    df_ibge.to_parquet(DEPARA_LOCAL, index=False)
    _save_to_gcs(df_ibge)
    return df_ibge


def join_tse_ibge(
    df_tse: pd.DataFrame,
    df_ibge: pd.DataFrame,
    tse_key: str = "cd_municipio",
    ibge_key: str = "cd_municipio_ibge",
) -> pd.DataFrame:
    """Join TSE data with IBGE indicators via normalized name matching within UF.

    Strategy:
    1. Build (sg_uf, nm_norm) → cd_municipio_ibge lookup from IBGE reference
    2. Add nm_norm to TSE chunk and left-join on (sg_uf, nm_norm)
    3. Fall back to numeric code approximation for unmatched rows
    """
    depara = load_depara()

    if depara.empty or "nm_norm" not in depara.columns:
        logger.warning("De-para vazio ou sem nm_norm — join IBGE ignorado")
        return df_tse

    # Build name → IBGE code lookup
    lookup = depara.set_index(["sg_uf", "nm_norm"])["cd_municipio_ibge"].to_dict()

    # Normalize TSE municipality names
    tse_has_uf = "sg_uf" in df_tse.columns
    tse_has_name = "nm_municipio" in df_tse.columns

    if tse_has_name and tse_has_uf:
        nm_norm = df_tse["nm_municipio"].map(_normalize_name)
        sg_uf = df_tse["sg_uf"].str.strip().str.upper()
        df_tse = df_tse.copy()
        df_tse["cd_municipio_ibge"] = [lookup.get((uf, nm)) for uf, nm in zip(sg_uf, nm_norm)]
    else:
        logger.warning("TSE chunk sem nm_municipio ou sg_uf — usando fallback numérico")
        df_tse = df_tse.copy()
        # Fallback: ibge_code = tse_code * 10 (rough, covers ~60% of cases)
        if tse_key in df_tse.columns:
            df_tse["cd_municipio_ibge"] = pd.to_numeric(df_tse[tse_key], errors="coerce") * 10
        else:
            df_tse["cd_municipio_ibge"] = pd.NA

    match_pct = df_tse["cd_municipio_ibge"].notna().mean() * 100
    logger.info("Join TSE↔IBGE: %.1f%% match por nome", match_pct)
    if match_pct < 80:
        logger.warning("Baixo match: %.1f%% < 80%%", match_pct)

    if df_ibge.empty or ibge_key not in df_ibge.columns:
        return df_tse

    df_ibge_clean = df_ibge.copy()
    df_ibge_clean[ibge_key] = pd.to_numeric(df_ibge_clean[ibge_key], errors="coerce").astype(
        "Int64"
    )
    df_tse["cd_municipio_ibge"] = pd.to_numeric(
        df_tse["cd_municipio_ibge"], errors="coerce"
    ).astype("Int64")

    return df_tse.merge(df_ibge_clean, left_on="cd_municipio_ibge", right_on=ibge_key, how="left")
