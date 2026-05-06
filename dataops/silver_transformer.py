"""Silver layer transformer: Bronze → Silver (clean, joined, schema-enforced)."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import pandas as pd

from dataops.depara_municipios import join_tse_ibge

logger = logging.getLogger("spepe.dataops.silver")

LOCAL_SILVER_DIR = Path(os.environ.get("DATA_DIR", "data")) / "silver"
LOCAL_BRONZE_DIR = Path(os.environ.get("DATA_DIR", "data")) / "bronze"
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")


def _read_gcs_parquet_glob(bucket_name: str, prefix: str) -> pd.DataFrame:
    """Read all parquet files under a GCS prefix into a single DataFrame."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = [b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".parquet")]
    if not blobs:
        return pd.DataFrame()
    frames = []
    for blob in blobs:
        data = blob.download_as_bytes()
        frames.append(pd.read_parquet(io.BytesIO(data)))
    return pd.concat(frames, ignore_index=True)


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

    df_ibge = _load_bronze_ibge(uf)

    # GCS+BQ path: stream parquet directly from GCS in 500k-row chunks to avoid OOM
    if use_bigquery and GCS_BUCKET:
        return _transform_streaming_to_bq(uf, year, df_ibge)

    # Local path: load full DataFrame (small UFs only)
    df_tse = _load_bronze_tse(uf, year)
    if df_tse.empty:
        return {
            "status": "error",
            "message": f"Bronze TSE não encontrado para {uf}/{year}",
        }

    df_tse = _normalize_tse(df_tse, year)
    df_joined = join_tse_ibge(df_tse, df_ibge)
    df_clean = _enforce_silver_schema(df_joined, uf, year)

    dq_result = _run_dq_checks(df_clean, uf, year)

    path = _write_local_silver(df_clean, uf, year)

    return {
        "status": "ok",
        "path": path,
        "rows": len(df_clean),
        "dq_score": dq_result["score"],
        "dq_warnings": dq_result["warnings"],
        "match_pct": float(
            df_clean.get("cd_municipio_ibge", pd.Series(dtype=object)).notna().mean() * 100
        ),
    }


def _transform_streaming_to_bq(uf: str, year: int, df_ibge: pd.DataFrame) -> dict:
    """Stream Bronze TSE from GCS in chunks → normalize → GCS temp parquet → BQ load.

    Uses GCS as staging to avoid load_table_from_dataframe memory spikes.
    """
    import gc
    import io
    import uuid

    import pyarrow.fs as pafs
    import pyarrow.parquet as pq

    from google.cloud import bigquery, storage

    gcs_path = f"{GCS_BUCKET}/raw/tse/{year}/{uf.upper()}/resultados_{uf.upper()}_{year}.parquet"
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    table_id = f"{project}.{dataset}.tse_{uf.lower()}_{year}"

    try:
        gcs_fs = pafs.GcsFileSystem()
        pf = pq.ParquetFile(gcs_path, filesystem=gcs_fs)
    except Exception as exc:
        return {"status": "error", "message": f"Bronze TSE GCS não encontrado: {exc}"}

    # Stage normalized batches as parquet on GCS → single BQ load_table_from_uri
    run_id = uuid.uuid4().hex[:8]
    staging_prefix = f"tmp/silver/{uf.lower()}_{year}_{run_id}"
    gcs_client = storage.Client()
    bucket_obj = gcs_client.bucket(GCS_BUCKET)

    total_rows = 0
    dq_ok_rows = 0
    bq_schema = None
    staged_uris: list[str] = []

    for i, batch in enumerate(pf.iter_batches(batch_size=100_000)):
        chunk = batch.to_pandas()
        del batch
        chunk = _normalize_tse(chunk, year)
        chunk = join_tse_ibge(chunk, df_ibge)
        _enforce_silver_schema_inplace(chunk, uf, year)
        _normalize_for_bq_inplace(chunk)
        chunk["ingested_at"] = pd.Timestamp.utcnow()

        if bq_schema is None:
            bq_schema = _dataframe_to_bq_schema(chunk)

        if "qt_votos" in chunk.columns:
            dq_ok_rows += int(chunk["qt_votos"].notna().sum())
        else:
            dq_ok_rows += len(chunk)
        total_rows += len(chunk)

        # Write chunk to GCS staging (avoids load_table_from_dataframe peak memory)
        buf = io.BytesIO()
        chunk.to_parquet(buf, index=False, compression="zstd")
        buf.seek(0)
        blob_name = f"{staging_prefix}/part_{i:04d}.parquet"
        bucket_obj.blob(blob_name).upload_from_file(buf)
        staged_uris.append(f"gs://{GCS_BUCKET}/{blob_name}")
        del chunk, buf
        gc.collect()
        logger.debug("Staged batch %d (%d cumulative rows)", i, total_rows)

    if total_rows == 0:
        return {"status": "error", "message": f"Bronze TSE vazio para {uf}/{year}"}

    # Single BQ load from GCS (memory-efficient)
    bq_client = bigquery.Client(project=project)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        create_disposition="CREATE_IF_NEEDED",
        source_format=bigquery.SourceFormat.PARQUET,
        schema=bq_schema,
    )
    uri_pattern = f"gs://{GCS_BUCKET}/{staging_prefix}/part_*.parquet"
    job = bq_client.load_table_from_uri(uri_pattern, table_id, job_config=job_config)
    job.result()
    logger.info("BQ load from URI: %s → %s (%d rows)", uri_pattern, table_id, total_rows)

    # Clean up staging files
    for uri in staged_uris:
        blob_name = uri.removeprefix(f"gs://{GCS_BUCKET}/")
        try:
            bucket_obj.blob(blob_name).delete()
        except Exception:
            pass

    if total_rows == 0:
        return {"status": "error", "message": f"Bronze TSE vazio para {uf}/{year}"}

    dq_score = dq_ok_rows / total_rows * 100
    logger.info(
        "Silver streaming BQ: %s → %s (%d rows, DQ=%.1f%%)",
        gcs_path,
        table_id,
        total_rows,
        dq_score,
    )
    return {
        "status": "ok",
        "path": table_id,
        "rows": total_rows,
        "dq_score": dq_score,
        "dq_warnings": [],
        "match_pct": 0.0,
    }


def _load_bronze_tse(uf: str, year: int) -> pd.DataFrame:
    if GCS_BUCKET:
        prefix = f"raw/tse/{year}/{uf.upper()}/"
        df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
        if not df.empty:
            return df

    bronze_path = LOCAL_BRONZE_DIR / "tse" / str(year) / uf.upper()
    files = list(bronze_path.glob("*.parquet")) if bronze_path.exists() else []
    if not files:
        legacy = Path(f"data/tse/resultados_{uf.upper()}_{year}.parquet")
        if legacy.exists():
            return pd.read_parquet(legacy)
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _load_bronze_ibge(uf: str) -> pd.DataFrame:
    if GCS_BUCKET:
        prefix = "raw/ibge/"
        df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
        if not df.empty:
            df_uf = (
                df[df.get("sg_uf", pd.Series(dtype=str)).str.upper() == uf.upper()]
                if "sg_uf" in df.columns
                else df
            )
            if not df_uf.empty:
                return df_uf

    ibge_dir = LOCAL_BRONZE_DIR / "ibge"
    # Load only the wide-format municipios file (not tall-format indicadores)
    files = list(ibge_dir.rglob(f"municipios_{uf.upper()}.parquet")) if ibge_dir.exists() else []
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _normalize_tse(df: pd.DataFrame, year: int) -> pd.DataFrame:
    from dataops.clients.tse_client import normalize_columns

    return normalize_columns(df, year)


def _enforce_silver_schema(df: pd.DataFrame, uf: str, year: int) -> pd.DataFrame:
    df = df.copy()
    _enforce_silver_schema_inplace(df, uf, year)
    return df


def _enforce_silver_schema_inplace(df: pd.DataFrame, uf: str, year: int) -> None:
    if "sg_uf" not in df.columns:
        df["sg_uf"] = uf.upper()
    if "ano_eleicao" not in df.columns:
        df["ano_eleicao"] = year
    if "qt_votos" in df.columns:
        df["qt_votos"] = pd.to_numeric(df["qt_votos"], errors="coerce").fillna(0).astype(int)


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


def _normalize_for_bq(df: pd.DataFrame) -> pd.DataFrame:
    """Convert pandas extension types (Int64, Float64, boolean) to numpy types for BQ upload."""
    df = df.copy()
    _normalize_for_bq_inplace(df)
    return df


def _normalize_for_bq_inplace(df: pd.DataFrame) -> None:
    for col in df.columns:
        dtype = df[col].dtype
        if hasattr(dtype, "numpy_dtype"):
            # pandas extension types (Int64, Float64, boolean)
            if pd.api.types.is_integer_dtype(dtype) or pd.api.types.is_float_dtype(dtype):
                df[col] = df[col].astype("float64")
            elif pd.api.types.is_bool_dtype(dtype):
                df[col] = df[col].astype("object")
        elif dtype.kind in ("i", "u"):
            # numpy signed/unsigned int — convert to float64 for BQ FLOAT64 schema
            df[col] = df[col].astype("float64")
        elif hasattr(df[col], "cat"):
            df[col] = df[col].astype("object")


def _write_bigquery(df: pd.DataFrame, table_name: str) -> str:
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.{table_name}"
        df = _normalize_for_bq(df)

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


def _write_bigquery_pesquisas(df: pd.DataFrame, year: int) -> str:
    """WRITE_APPEND com pre-delete por ano — preserva dados de outros anos."""
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    table_id = f"{project}.{dataset}.fact_pesquisa"

    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    df = _normalize_for_bq(df)
    if "ingested_at" not in df.columns:
        df = df.copy()
        df["ingested_at"] = pd.Timestamp.utcnow()

    # Remove rows for this year before appending to avoid duplicates on re-run
    try:
        client.query(f"DELETE FROM `{table_id}` WHERE ano = {year}").result()
        logger.info("Pre-delete fact_pesquisa ano=%d OK", year)
    except Exception:
        pass  # Table may not exist yet — CREATE_IF_NEEDED handles it

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        create_disposition="CREATE_IF_NEEDED",
        autodetect=True,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
    )
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    logger.info("Pesquisas Silver BQ: %s ano=%d (%d rows)", table_id, year, len(df))
    return table_id


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

    gcs_prefix = f"raw/pesquisas/{year}/BR"
    if GCS_BUCKET:
        try:
            df_gcs = _read_gcs_parquet_glob(GCS_BUCKET, gcs_prefix)
            if not df_gcs.empty:
                frames.append(df_gcs)
                logger.info("Pesquisas Bronze GCS: %d rows (prefix=%s)", len(df_gcs), gcs_prefix)
        except Exception as exc:
            logger.warning("Falha ao ler pesquisas do GCS: %s", exc)

    if not frames:
        for pattern in (f"pesquisas_tse_{year}.parquet", f"pesquisas_atlas_{year}.parquet"):
            f = bronze_dir / pattern
            if f.exists():
                try:
                    df_part = pd.read_parquet(f)
                    frames.append(df_part)
                    logger.info("Pesquisas Bronze local: %s (%d rows)", f.name, len(df_part))
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
    _corrente_year = int(os.environ.get("PESQUISA_CORRENTE_YEAR", "2026"))
    df["tipo_pesquisa"] = "corrente" if year == _corrente_year else "historica"
    df["ingested_at"] = pd.Timestamp.utcnow()

    if use_bigquery:
        path = _write_bigquery_pesquisas(df, year)
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
    """Transform Bronze social data (Twitter/X + Facebook + YouTube + Bluesky + GDELT + RSS) to Silver.

    Reads: raw/social/{year}/BR/ (GCS) or local bronze/social/{year}/BR/
    Writes: Silver table `social_mencoes_br` with candidato × semana × sentiment × score_confiabilidade.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []

    if GCS_BUCKET:
        prefix = f"raw/social/{year}/BR/"
        df_gcs = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
        if not df_gcs.empty:
            frames.append(df_gcs)
            logger.info("Social Bronze GCS: %d registros (ano=%d)", len(df_gcs), year)
    else:
        bronze_social = LOCAL_BRONZE_DIR / "social" / str(year) / "BR"
        patterns = [
            "twitter_mencoes_*.parquet",
            "facebook_posts_*.parquet",
            "youtube_videos_*.parquet",
            "bluesky_posts_*.parquet",
            "gdelt_noticias_*.parquet",
            "rss_noticias_*.parquet",
        ]
        for pattern in patterns:
            for f in bronze_social.glob(pattern) if bronze_social.exists() else []:
                try:
                    frames.append(pd.read_parquet(f))
                    logger.info("Social Bronze local: %s (%d rows)", f.name, len(frames[-1]))
                except Exception as exc:
                    logger.warning("Falha ao ler %s: %s", f, exc)

    if not frames:
        return {"status": "error", "message": f"Bronze social vazio para {year}"}

    df = pd.concat(frames, ignore_index=True)

    # Normaliza timestamp para UTC → colunas de semana e data
    for col in ("created_at", "created_time", "published_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
            break

    ts_col = next(
        (c for c in ("created_at", "created_time", "published_at") if c in df.columns), None
    )
    if ts_col:
        df["created_at"] = df[ts_col]
    else:
        df["created_at"] = pd.NaT

    df["data_referencia"] = pd.to_datetime(df["created_at"], errors="coerce").dt.date
    df["semana"] = (
        pd.to_datetime(df["created_at"], errors="coerce").dt.isocalendar().week.astype("Int64")
    )
    df["ano_semana"] = (
        pd.to_datetime(df["created_at"], errors="coerce")
        .dt.strftime("%Y-W%V")
        .fillna(f"{year}-W00")
    )

    # Candidato canônico — Twitter/X já traz; YouTube/FB pode não ter
    if "candidato" not in df.columns:
        df["candidato"] = "desconhecido"
    else:
        df["candidato"] = df["candidato"].fillna("desconhecido").str.strip()

    # Fonte canônica
    if "fonte" not in df.columns:
        df["fonte"] = "social"

    # Texto canônico
    for txt_col in ("text", "message", "title", "description"):
        if txt_col in df.columns:
            df["text"] = df[txt_col].fillna("")
            break
    if "text" not in df.columns:
        df["text"] = ""

    # Sentimento canônico (positivo | negativo | neutro)
    if "sentiment" not in df.columns:
        df["sentiment"] = "neutro"
    df["sentiment"] = (
        df["sentiment"]
        .str.lower()
        .replace({"positive": "positivo", "negative": "negativo", "neutral": "neutro"})
        .fillna("neutro")
    )

    # UF — best-effort: campo direto ou inferido do location
    if "sg_uf" not in df.columns:
        for uf_col in ("uf", "state", "user_location"):
            if uf_col in df.columns:
                df["sg_uf"] = df[uf_col].str.upper().str[:2]
                break
        else:
            df["sg_uf"] = ""

    # Métricas numéricas
    for col in (
        "like_count",
        "retweet_count",
        "reply_count",
        "likes",
        "comments",
        "shares",
        "view_count",
        "comment_count",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["ano"] = year
    df["ingested_at"] = pd.Timestamp.utcnow()

    # Source reliability metadata — enrich if not already present from Bronze
    from dataops.source_registry import get_source_meta

    if "score_confiabilidade" not in df.columns:
        df["score_confiabilidade"] = df["fonte"].apply(
            lambda f: get_source_meta(str(f) if f else "desconhecido").score_confiabilidade
        )
    if "tipo_fonte" not in df.columns:
        df["tipo_fonte"] = df["fonte"].apply(
            lambda f: get_source_meta(str(f) if f else "desconhecido").tipo_fonte
        )
    if "vies_politico" not in df.columns:
        df["vies_politico"] = df["fonte"].apply(
            lambda f: get_source_meta(str(f) if f else "desconhecido").vies_politico
        )
    if "alcance_fonte" not in df.columns:
        df["alcance_fonte"] = df["fonte"].apply(
            lambda f: get_source_meta(str(f) if f else "desconhecido").alcance
        )

    # Mantém apenas colunas necessárias para o Silver
    keep = [
        "candidato",
        "fonte",
        "sg_uf",
        "text",
        "sentiment",
        "created_at",
        "data_referencia",
        "semana",
        "ano_semana",
        "ano",
        "like_count",
        "retweet_count",
        "reply_count",
        "view_count",
        "comment_count",
        "score_confiabilidade",
        "tipo_fonte",
        "vies_politico",
        "alcance_fonte",
        "ingested_at",
    ]
    df = df[[c for c in keep if c in df.columns]]

    if use_bigquery:
        project = os.environ.get("GCP_PROJECT_ID", "")
        dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
        table_id = f"{project}.{dataset}.social_mencoes_br"
        from google.cloud import bigquery
        from google.cloud.bigquery import SchemaUpdateOption, WriteDisposition

        client = bigquery.Client(project=project)
        client.query(
            f"DELETE FROM `{table_id}` WHERE ano = {year}", job_config=bigquery.QueryJobConfig()
        ).result()
        job_config = bigquery.LoadJobConfig(
            write_disposition=WriteDisposition.WRITE_APPEND,
            autodetect=True,
            schema_update_options=[SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        )
        client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
        path = table_id
        logger.info("Social Silver BQ: %s (%d rows)", path, len(df))
    else:
        path_local = LOCAL_SILVER_DIR / f"social_mencoes_br_{year}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Social Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_seguranca_to_silver(
    uf: str,
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze security data (IVS, Atlas da Violência, SINESP) to Silver.

    Reads:  bronze/security/{year}/{UF}/seguranca_{UF}_{year}.parquet
    Writes: Silver table `seguranca_municipal` (BigQuery) or local parquet.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    bronze_path = LOCAL_BRONZE_DIR / "security" / str(year) / uf.upper()
    files = (
        list(bronze_path.glob(f"seguranca_{uf.upper()}_{year}.parquet"))
        if bronze_path.exists()
        else []
    )

    if not files and GCS_BUCKET:
        prefix = f"raw/security/{year}/{uf.upper()}/"
        df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
    elif files:
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    else:
        return {"status": "error", "message": f"Bronze segurança vazio para {uf}/{year}"}

    if df.empty:
        return {"status": "error", "message": f"Bronze segurança vazio para {uf}/{year}"}

    df = df.copy()
    if "sg_uf" not in df.columns:
        df["sg_uf"] = uf.upper()
    if "ano" not in df.columns:
        df["ano"] = year

    for col in ("taxa_homicidio", "ivs_valor", "n_ocorrencias"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
            "float64"
        )

    df["ingested_at"] = pd.Timestamp.utcnow()

    table_name = f"seguranca_municipal_{uf.lower()}_{year}"
    if use_bigquery:
        path = _write_bigquery(df, "seguranca_municipal")
    else:
        path_local = LOCAL_SILVER_DIR / f"{table_name}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Segurança Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_saude_to_silver(
    uf: str,
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze DataSUS health data to Silver.

    Reads:  bronze/datasus/{year}/{UF}/saude_{UF}_{year}.parquet
    Writes: Silver table `saude_municipal` (BigQuery) or local parquet.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    bronze_path = LOCAL_BRONZE_DIR / "datasus" / str(year) / uf.upper()
    files = (
        list(bronze_path.glob(f"saude_{uf.upper()}_{year}.parquet")) if bronze_path.exists() else []
    )

    if not files and GCS_BUCKET:
        prefix = f"raw/datasus/{year}/{uf.upper()}/"
        df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
    elif files:
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    else:
        return {"status": "error", "message": f"Bronze DataSUS vazio para {uf}/{year}"}

    if df.empty:
        return {"status": "error", "message": f"Bronze DataSUS vazio para {uf}/{year}"}

    df = df.copy()
    if "sg_uf" not in df.columns:
        df["sg_uf"] = uf.upper()
    if "ano" not in df.columns:
        df["ano"] = year

    for col in ("tx_mortalidade_infantil", "cobertura_esf_pct", "leitos_per_1000"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
            "float64"
        )

    df["ingested_at"] = pd.Timestamp.utcnow()

    table_name = f"saude_municipal_{uf.lower()}_{year}"
    if use_bigquery:
        path = _write_bigquery(df, "saude_municipal")
    else:
        path_local = LOCAL_SILVER_DIR / f"{table_name}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Saúde Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_economia_to_silver(
    uf: str,
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze economia data (DIEESE + CETIC) to Silver.

    Reads:  bronze/economia/{year}/{UF}/economia_{UF}_{year}.parquet
            OR builds from DIEESE/CETIC clients if Bronze not found.
    Writes: Silver table `economia_municipal` (BigQuery) or local parquet.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    bronze_path = LOCAL_BRONZE_DIR / "economia" / str(year) / uf.upper()
    files = (
        list(bronze_path.glob(f"economia_{uf.upper()}_{year}.parquet"))
        if bronze_path.exists()
        else []
    )

    if not files and GCS_BUCKET:
        prefix = f"raw/economia/{year}/{uf.upper()}/"
        df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
    elif files:
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    else:
        # Build from DIEESE + CETIC APIs directly (no pre-ingested Bronze)
        from dataops.clients.cetic_client import build_digital_access_dataframe
        from dataops.clients.dieese_client import build_cesta_basica_dataframe
        from dataops.clients.ibge_client import load_municipios

        municipios = load_municipios(uf)
        municipios_ibge = municipios["cd_municipio_ibge"].dropna().astype(int).tolist()

        df_dieese = build_cesta_basica_dataframe(uf, year, municipios_ibge)
        df_cetic = build_digital_access_dataframe(uf, year, municipios_ibge)

        frames = [f for f in [df_dieese, df_cetic] if not f.empty]
        if not frames:
            return {"status": "error", "message": f"Sem dados economia para {uf}/{year}"}

        if len(frames) == 2:
            df = frames[0].merge(
                frames[1].drop(columns=["sg_uf", "fontes"], errors="ignore"),
                on="cd_municipio_ibge",
                how="outer",
            )
        else:
            df = frames[0]

    if df.empty:
        return {"status": "error", "message": f"Bronze economia vazio para {uf}/{year}"}

    df = df.copy()
    if "sg_uf" not in df.columns:
        df["sg_uf"] = uf.upper()
    if "ano" not in df.columns:
        df["ano"] = year

    for col in (
        "cesta_basica_capital_brl",
        "variacao_cesta_mensal_pct",
        "horas_trabalho_cesta",
        "pct_internet_domiciliar",
        "pct_computador_domiciliar",
        "pct_smartphone_domiciliar",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
            "float64"
        )

    df["ingested_at"] = pd.Timestamp.utcnow()

    table_name = f"economia_municipal_{uf.lower()}_{year}"
    if use_bigquery:
        path = _write_bigquery(df, "economia_municipal")
    else:
        path_local = LOCAL_SILVER_DIR / f"{table_name}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Economia Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_cadunico_to_silver(
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze CadÚnico + Bolsa Família data to Silver.

    Reads:  raw/cadunico/{year}/BR/cadunico_BR_{year}.parquet  (GCS or local)
    Writes: Silver table `transferencias_sociais` (BigQuery WRITE_APPEND pre-delete) or local parquet.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    bronze_path = LOCAL_BRONZE_DIR / "cadunico" / str(year) / "BR"
    files = list(bronze_path.glob(f"cadunico_BR_{year}.parquet")) if bronze_path.exists() else []

    if not files and GCS_BUCKET:
        prefix = f"raw/cadunico/{year}/BR/"
        logger.info("CadÚnico Bronze GCS: prefix=%s", prefix)
        df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
    elif files:
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    else:
        return {"status": "error", "message": f"Bronze CadÚnico vazio para ano={year}"}

    if df.empty:
        return {"status": "error", "message": f"Bronze CadÚnico vazio para ano={year}"}

    df = df.copy()
    if "ano" not in df.columns:
        df["ano"] = year

    for col in (
        "qtd_beneficiarios_bolsa_familia",
        "valor_total_bolsa_familia_reais",
        "qtd_familias_cadunico",
        "qtd_familias_extrema_pobreza",
        "qtd_familias_baixa_renda",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
            "float64"
        )

    df["ingested_at"] = pd.Timestamp.utcnow()

    logger.info("CadÚnico Silver %d: %d municípios", year, len(df))

    if use_bigquery:
        project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
        dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=project)
            table_id = f"{project}.{dataset}.transferencias_sociais"
            df_bq = _normalize_for_bq(df)

            # Pre-delete year slice to allow idempotent re-runs
            try:
                client.query(f"DELETE FROM `{table_id}` WHERE ano = {year}").result()
                logger.info("CadÚnico Silver pre-delete ano=%d OK", year)
            except Exception:
                pass  # table may not exist yet

            job_config = bigquery.LoadJobConfig(
                write_disposition="WRITE_APPEND",
                create_disposition="CREATE_IF_NEEDED",
                autodetect=True,
                schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            )
            job = client.load_table_from_dataframe(df_bq, table_id, job_config=job_config)
            job.result()
            logger.info("CadÚnico Silver BigQuery: %s (%d rows)", table_id, len(df))
            path = table_id
        except ImportError:
            logger.warning("google-cloud-bigquery não disponível. Usando local.")
            path_local = LOCAL_SILVER_DIR / f"transferencias_sociais_{year}.parquet"
            df.to_parquet(path_local, index=False, compression="zstd")
            path = str(path_local)
    else:
        path_local = LOCAL_SILVER_DIR / f"transferencias_sociais_{year}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("CadÚnico Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_digital_to_silver(year: int, use_bigquery: bool = False) -> dict:
    """Transform Bronze digital data (Meta Ads + Google Trends) to Silver layer.

    Meta Ads Bronze files:
      digital/{year}/BR/meta_ads_{year}.parquet         → Silver meta_ads_BR
      digital/{year}/BR/meta_ads_regioes_{year}.parquet → Silver meta_ads_regioes_BR

    Google Trends Bronze files:
      digital/{year}/BR/google_trends_timeline_{year}.parquet → Silver google_trends_BR
      digital/{year}/BR/google_trends_por_uf_{year}.parquet   → Silver google_trends_uf_BR

    All tables include score_confiabilidade from source_registry.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    from dataops.source_registry import get_source_meta

    results: dict[str, dict] = {}

    def _read_bronze(filename: str) -> pd.DataFrame:
        if GCS_BUCKET:
            prefix = f"raw/digital/{year}/BR/{filename}"
            try:
                from google.cloud import storage
                client = storage.Client()
                bucket = client.bucket(GCS_BUCKET)
                blob = bucket.blob(prefix)
                if blob.exists():
                    import io
                    return pd.read_parquet(io.BytesIO(blob.download_as_bytes()))
            except Exception as exc:
                logger.warning("GCS read %s: %s", prefix, exc)
        local = LOCAL_BRONZE_DIR / "digital" / str(year) / "BR" / filename
        if local.exists():
            return pd.read_parquet(local)
        return pd.DataFrame()

    def _write_silver(df: pd.DataFrame, table_name: str) -> str:
        if df.empty:
            return ""
        if use_bigquery:
            project = os.environ.get("GCP_PROJECT_ID", "")
            dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
            table_id = f"{project}.{dataset}.{table_name}"
            from google.cloud import bigquery
            from google.cloud.bigquery import SchemaUpdateOption, WriteDisposition
            bq = bigquery.Client(project=project)
            bq.query(f"DELETE FROM `{table_id}` WHERE ano = {year}",
                     job_config=bigquery.QueryJobConfig()).result()
            cfg = bigquery.LoadJobConfig(
                write_disposition=WriteDisposition.WRITE_APPEND,
                autodetect=True,
                schema_update_options=[SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            )
            bq.load_table_from_dataframe(df, table_id, job_config=cfg).result()
            logger.info("Digital Silver BQ %s: %d rows", table_id, len(df))
            return table_id
        path = LOCAL_SILVER_DIR / f"{table_name}_{year}.parquet"
        df.to_parquet(path, index=False, compression="zstd")
        logger.info("Digital Silver local %s: %d rows", path, len(df))
        return str(path)

    meta_score = get_source_meta("meta_ad_library") if "meta_ad_library" in __import__("dataops.source_registry", fromlist=["SOURCE_REGISTRY"]).SOURCE_REGISTRY else None

    # ── Meta Ads summary ──────────────────────────────────────────────────────
    ads_df = _read_bronze(f"meta_ads_{year}.parquet")
    if not ads_df.empty:
        ads_df["ano"] = year
        ads_df["ingested_at"] = pd.Timestamp.utcnow()
        ads_df["score_confiabilidade"] = 8.0
        ads_df["tipo_fonte"] = "agregador"
        ads_df["vies_politico"] = "neutro"
        results["meta_ads_BR"] = {
            "path": _write_silver(ads_df, "meta_ads_BR"),
            "rows": len(ads_df),
        }

    # ── Meta Ads por UF (region_distribution explodido) ───────────────────────
    regions_df = _read_bronze(f"meta_ads_regioes_{year}.parquet")
    if not regions_df.empty:
        regions_df["ano"] = year
        regions_df["ingested_at"] = pd.Timestamp.utcnow()
        regions_df["score_confiabilidade"] = 8.0
        regions_df["tipo_fonte"] = "agregador"
        results["meta_ads_regioes_BR"] = {
            "path": _write_silver(regions_df, "meta_ads_regioes_BR"),
            "rows": len(regions_df),
        }

    # ── Meta Ads demográfico ───────────────────────────────────────────────────
    demo_df = _read_bronze(f"meta_ads_demograficos_{year}.parquet")
    if not demo_df.empty:
        demo_df["ano"] = year
        demo_df["ingested_at"] = pd.Timestamp.utcnow()
        results["meta_ads_demograficos_BR"] = {
            "path": _write_silver(demo_df, "meta_ads_demograficos_BR"),
            "rows": len(demo_df),
        }

    # ── Google Trends timeline ─────────────────────────────────────────────────
    trends_df = _read_bronze(f"google_trends_timeline_{year}.parquet")
    if not trends_df.empty:
        # Pivot wide → long: one row per (date, candidato)
        ts_col = "date" if "date" in trends_df.columns else trends_df.columns[0]
        id_cols = [c for c in ("date", "ano", "fonte") if c in trends_df.columns]
        val_cols = [c for c in trends_df.columns if c not in id_cols]
        long_df = trends_df.melt(id_vars=id_cols, value_vars=val_cols,
                                  var_name="candidato", value_name="interesse_busca")
        long_df["ano"] = year
        long_df["fonte"] = "google_trends"
        long_df["score_confiabilidade"] = 7.0
        long_df["tipo_fonte"] = "agregador"
        long_df["vies_politico"] = "neutro"
        long_df["ingested_at"] = pd.Timestamp.utcnow()
        results["google_trends_BR"] = {
            "path": _write_silver(long_df, "google_trends_BR"),
            "rows": len(long_df),
        }

    # ── Google Trends por UF ──────────────────────────────────────────────────
    trends_uf_df = _read_bronze(f"google_trends_por_uf_{year}.parquet")
    if not trends_uf_df.empty:
        trends_uf_df["ano"] = year
        trends_uf_df["score_confiabilidade"] = 7.0
        trends_uf_df["tipo_fonte"] = "agregador"
        trends_uf_df["ingested_at"] = pd.Timestamp.utcnow()
        results["google_trends_uf_BR"] = {
            "path": _write_silver(trends_uf_df, "google_trends_uf_BR"),
            "rows": len(trends_uf_df),
        }

    if not results:
        return {"status": "error", "message": f"Nenhum dado digital Bronze para {year}"}

    return {"status": "ok", "tables": results}


def _dataframe_to_bq_schema(df: pd.DataFrame) -> list:
    from google.cloud import bigquery

    _type_map = {
        "int64": "INT64",
        "int32": "INT64",
        "float64": "FLOAT64",
        "float32": "FLOAT64",
        "bool": "BOOL",
        "object": "STRING",
    }
    fields = []
    for col, dtype in df.dtypes.items():
        dtype_str = str(dtype)
        if dtype_str.startswith("datetime64"):
            bq_type = "TIMESTAMP"
        elif dtype_str == "date":
            bq_type = "DATE"
        else:
            bq_type = _type_map.get(dtype_str, "STRING")
        fields.append(bigquery.SchemaField(col, bq_type, mode="NULLABLE"))
    return fields
