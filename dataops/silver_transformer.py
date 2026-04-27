"""Silver layer transformer: Bronze → Silver (clean, joined, schema-enforced)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from dataops.depara_municipios import join_tse_ibge

logger = logging.getLogger("spepe.dataops.silver")

LOCAL_SILVER_DIR = Path(os.environ.get("DATA_DIR", "data")) / "silver"
LOCAL_BRONZE_DIR = Path(os.environ.get("DATA_DIR", "data")) / "bronze"

CANONICAL_TSE_COLS = [
    "sg_uf",
    "cd_municipio",
    "nm_municipio",
    "nr_zona",
    "nr_secao",
    "nr_candidato",
    "nm_candidato",
    "qt_votos",
    "ds_cargo",
    "cd_cargo",
    "nr_turno",
]


def transform_to_silver(
    uf: str,
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze TSE + IBGE data to Silver layer."""
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    df_tse = _load_bronze_tse(uf, year)
    df_ibge = _load_bronze_ibge(uf)

    if df_tse.empty:
        return {
            "status": "error",
            "message": f"Bronze TSE não encontrado para {uf}/{year}",
        }

    df_tse = _normalize_tse(df_tse, year)
    df_joined = join_tse_ibge(df_tse, df_ibge)
    df_clean = _enforce_silver_schema(df_joined, uf, year)

    dq_result = _run_dq_checks(df_clean, uf, year)

    if use_bigquery:
        path = _write_bigquery(df_clean, f"tse_{uf.lower()}_{year}")
    else:
        path = _write_local_silver(df_clean, uf, year)

    return {
        "status": "ok",
        "path": path,
        "rows": len(df_clean),
        "dq_score": dq_result["score"],
        "dq_warnings": dq_result["warnings"],
        "match_pct": float(df_clean["cd_municipio_ibge"].notna().mean() * 100),
    }


def _load_bronze_tse(uf: str, year: int) -> pd.DataFrame:
    bronze_path = LOCAL_BRONZE_DIR / "tse" / str(year) / uf.upper()
    files = list(bronze_path.glob("*.parquet")) if bronze_path.exists() else []

    if not files:
        legacy = Path(f"data/tse/resultados_{uf.upper()}_{year}.parquet")
        if legacy.exists():
            return pd.read_parquet(legacy)
        return pd.DataFrame()

    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _load_bronze_ibge(uf: str) -> pd.DataFrame:
    ibge_dir = LOCAL_BRONZE_DIR / "ibge"
    files = list(ibge_dir.glob(f"*{uf.upper()}*.parquet")) if ibge_dir.exists() else []
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _normalize_tse(df: pd.DataFrame, year: int) -> pd.DataFrame:
    from dataops.clients.tse_client import normalize_columns

    return normalize_columns(df, year)


def _enforce_silver_schema(df: pd.DataFrame, uf: str, year: int) -> pd.DataFrame:
    df = df.copy()
    if "sg_uf" not in df.columns:
        df["sg_uf"] = uf.upper()
    if "ano_eleicao" not in df.columns:
        df["ano_eleicao"] = year
    if "qt_votos" in df.columns:
        df["qt_votos"] = pd.to_numeric(df["qt_votos"], errors="coerce").fillna(0).astype(int)
    return df


def _run_dq_checks(df: pd.DataFrame, uf: str, year: int) -> dict:
    warnings = []
    checks_passed = 0
    checks_total = 4

    if "qt_votos" in df.columns:
        null_votos = df["qt_votos"].isna().sum()
        if null_votos == 0:
            checks_passed += 1
        else:
            warnings.append(f"qt_votos: {null_votos} nulos")

    if "cd_municipio" in df.columns:
        null_mun = df["cd_municipio"].isna().sum()
        if null_mun == 0:
            checks_passed += 1
        else:
            warnings.append(f"cd_municipio: {null_mun} nulos")
    else:
        warnings.append("Coluna cd_municipio ausente")

    if "cd_municipio_ibge" in df.columns:
        match_pct = df["cd_municipio_ibge"].notna().mean() * 100
        if match_pct >= 95:
            checks_passed += 1
        else:
            warnings.append(f"Match TSE↔IBGE: {match_pct:.1f}% < 95%")
    else:
        warnings.append("Join IBGE não realizado")

    if len(df) > 0:
        checks_passed += 1
    else:
        warnings.append("Dataset vazio")

    score = checks_passed / checks_total * 100
    if score < 95:
        logger.warning(f"DQ score {score:.0f}% < 95% para {uf}/{year}: {warnings}")

    return {"score": score, "warnings": warnings}


def _write_local_silver(df: pd.DataFrame, uf: str, year: int) -> str:
    path = LOCAL_SILVER_DIR / f"tse_{uf.lower()}_{year}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    logger.info(f"Silver escrito: {path} ({len(df)} rows)")
    return str(path)


def _write_bigquery(df: pd.DataFrame, table_name: str) -> str:
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.{table_name}"

        if "ingested_at" not in df.columns:
            df = df.copy()
            df["ingested_at"] = pd.Timestamp.utcnow()

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            create_disposition="CREATE_IF_NEEDED",
            autodetect=False,
            schema=_dataframe_to_bq_schema(df),
        )
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        logger.info("Silver BigQuery: %s (%d rows)", table_id, len(df))
        return table_id
    except ImportError:
        logger.warning("google-cloud-bigquery não disponível. Usando local.")
        # table_name format: tse_{uf}_{year} — uf pode ter underscore, year é sempre 4 dígitos no fim
        parts = table_name.rsplit("_", 2)
        if len(parts) == 3:
            return _write_local_silver(df, parts[1], int(parts[2]))
        return _write_local_silver(df, "BR", 2022)


def transform_pesquisas_to_silver(
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze polls (TSE PesqEle + Atlas) to Silver fact_pesquisa.

    Reads:  bronze/pesquisas/{year}/BR/pesquisas_tse_{year}.parquet
            bronze/pesquisas/{year}/BR/pesquisas_atlas_{year}.parquet
            bronze/pesquisas/{year}/BR/dim_instituto.parquet
    Writes: Silver table `fact_pesquisa` (BigQuery) or local parquet.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    bronze_dir = LOCAL_BRONZE_DIR / "pesquisas" / str(year) / "BR"
    frames: list[pd.DataFrame] = []

    for pattern in (f"pesquisas_tse_{year}.parquet", f"pesquisas_atlas_{year}.parquet"):
        f = bronze_dir / pattern
        if f.exists():
            try:
                df_part = pd.read_parquet(f)
                frames.append(df_part)
                logger.info("Pesquisas Bronze lido: %s (%d rows)", f.name, len(df_part))
            except Exception as exc:
                logger.warning("Falha ao ler %s: %s", f, exc)

    if not frames:
        return {"status": "error", "message": f"Bronze pesquisas vazio para {year}"}

    df = pd.concat(frames, ignore_index=True)

    # Load house_effect from dim_instituto seed
    dim_path = bronze_dir / "dim_instituto.parquet"
    house_map: dict[str, float] = {}
    if dim_path.exists():
        try:
            df_dim = pd.read_parquet(dim_path)
            house_map = dict(
                zip(
                    df_dim["instituto"].str.lower(),
                    df_dim["house_effect_score"],
                )
            )
        except Exception as exc:
            logger.warning("Falha ao ler dim_instituto: %s", exc)

    # Apply house_effect adjustment
    if "instituto" in df.columns:
        df["house_effect"] = (
            df["instituto"]
            .str.lower()
            .map(lambda x: house_map.get(str(x).strip(), 0.0) if pd.notna(x) else 0.0)
        )
        if "intencao_pct" in df.columns:
            df["intencao_pct_num"] = pd.to_numeric(df["intencao_pct"], errors="coerce")
            df["intencao_ajustada"] = df["intencao_pct_num"] - df["house_effect"]
    else:
        df["house_effect"] = 0.0

    # Ensure record_confidence_score present
    if "record_confidence_score" not in df.columns:
        df["record_confidence_score"] = 0.50

    df["ano"] = year
    df["ingested_at"] = pd.Timestamp.utcnow()

    if use_bigquery:
        path = _write_bigquery(df, "fact_pesquisa")
    else:
        path_local = LOCAL_SILVER_DIR / f"fact_pesquisa_{year}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Pesquisas Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_social_to_silver(
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze social data (Twitter/X + Facebook) to Silver layer.

    Reads: raw/social/{year}/BR/twitter_mencoes_*.parquet
    Writes: Silver table `social_mencoes_br` (BigQuery) or local parquet.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    bronze_social = LOCAL_BRONZE_DIR / "social" / str(year) / "BR"

    patterns = [
        "twitter_mencoes_*.parquet",
        "facebook_posts_*.parquet",
    ]
    for pattern in patterns:
        files = list(bronze_social.glob(pattern)) if bronze_social.exists() else []
        for f in files:
            try:
                frames.append(pd.read_parquet(f))
                logger.info("Social Bronze lido: %s (%d rows)", f.name, len(frames[-1]))
            except Exception as exc:
                logger.warning("Falha ao ler %s: %s", f, exc)

    if not frames:
        return {"status": "error", "message": f"Bronze social vazio para {year}"}

    df = pd.concat(frames, ignore_index=True)

    # Normaliza timestamp para UTC
    for col in ("created_at", "created_time"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    # Garante coluna fonte
    if "fonte" not in df.columns:
        df["fonte"] = "desconhecido"

    # Coluna canônica de texto
    if "text" not in df.columns and "message" in df.columns:
        df["text"] = df["message"]

    # Métricas numéricas
    for col in ("like_count", "retweet_count", "reply_count", "likes", "comments", "shares"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["ano"] = year
    df["ingested_at"] = pd.Timestamp.utcnow()

    if use_bigquery:
        path = _write_bigquery(df, "social_mencoes_br")
    else:
        path_local = LOCAL_SILVER_DIR / f"social_mencoes_br_{year}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Social Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def _dataframe_to_bq_schema(df: pd.DataFrame) -> list:
    from google.cloud import bigquery

    _type_map = {
        "int64": "INT64",
        "int32": "INT64",
        "float64": "FLOAT64",
        "float32": "FLOAT64",
        "bool": "BOOL",
        "object": "STRING",
        "datetime64[ns]": "TIMESTAMP",
        "datetime64[ns, UTC]": "TIMESTAMP",
    }
    return [
        bigquery.SchemaField(col, _type_map.get(str(dtype), "STRING"), mode="NULLABLE")
        for col, dtype in df.dtypes.items()
    ]
