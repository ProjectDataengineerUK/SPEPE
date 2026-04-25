"""Bronze layer writer — immutable raw data to GCS (or local fallback)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.dataops.bronze")

LOCAL_BRONZE_DIR = Path(os.environ.get("DATA_DIR", "data")) / "bronze"
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")


def _gcs_path(source: str, year: int, uf: str, filename: str) -> str:
    return f"raw/{source}/{year}/{uf.upper()}/{filename}"


def write_bronze(
    df: pd.DataFrame,
    source: str,
    year: int,
    uf: str,
    filename: str,
    use_gcs: bool = False,
    base_path: str = None,
) -> str:
    """Write DataFrame to Bronze layer (immutable, partitioned by source/year/uf).

    Args:
        df: Data to write
        source: Data source (tse, ibge, digital, etc.)
        year: Year of data
        uf: State code (UF)
        filename: Output filename
        use_gcs: Write to GCS instead of local
        base_path: Override base directory for local writes (for testing)

    Returns the path where data was written.
    """
    if use_gcs and GCS_BUCKET:
        return _write_gcs(df, source, year, uf, filename)
    return _write_local(df, source, year, uf, filename, base_path=base_path)


def _write_local(df: pd.DataFrame, source: str, year: int, uf: str, filename: str, base_path: str = None) -> str:
    base = Path(base_path) if base_path else LOCAL_BRONZE_DIR
    out_dir = base / source / str(year) / uf.upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    if out_path.exists():
        logger.info(f"Bronze já existe (imutável): {out_path}")
        return str(out_path)

    df_with_meta = df.copy()
    df_with_meta["_ingested_at"] = datetime.now(timezone.utc).isoformat()
    df_with_meta["_source"] = source
    df_with_meta["_year"] = year
    df_with_meta["_uf"] = uf.upper()

    df_with_meta.to_parquet(out_path, index=False, compression="zstd")
    size_mb = out_path.stat().st_size / 1_048_576
    logger.info(f"Bronze escrito: {out_path} ({size_mb:.1f} MB, {len(df)} rows)")
    return str(out_path)


def _write_gcs(df: pd.DataFrame, source: str, year: int, uf: str, filename: str) -> str:
    try:
        from google.cloud import storage
        import io

        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        gcs_path = _gcs_path(source, year, uf, filename)
        blob = bucket.blob(gcs_path)

        if blob.exists():
            logger.info(f"Bronze GCS já existe (imutável): gs://{GCS_BUCKET}/{gcs_path}")
            return f"gs://{GCS_BUCKET}/{gcs_path}"

        df_with_meta = df.copy()
        df_with_meta["_ingested_at"] = datetime.now(timezone.utc).isoformat()
        df_with_meta["_source"] = source

        buf = io.BytesIO()
        df_with_meta.to_parquet(buf, index=False, compression="zstd")
        buf.seek(0)
        blob.upload_from_file(buf, content_type="application/octet-stream")
        full_path = f"gs://{GCS_BUCKET}/{gcs_path}"
        logger.info(f"Bronze GCS escrito: {full_path}")
        return full_path

    except ImportError:
        logger.warning("google-cloud-storage não disponível. Usando local.")
        return _write_local(df, source, year, uf, filename)
