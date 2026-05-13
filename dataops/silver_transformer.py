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


def _write_bigquery_uf_year(df: pd.DataFrame, table_name: str, uf: str, year: int) -> str:
    """WRITE_APPEND com pre-delete por uf+ano — preserva outras UFs/anos na mesma tabela."""
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

        # Apaga linhas desta UF+ano antes de inserir (evita duplicatas em re-runs)
        try:
            client.query(
                f"DELETE FROM `{table_id}` WHERE sg_uf = '{uf.upper()}' AND ano = {year}",
                job_config=bigquery.QueryJobConfig(),
            ).result()
        except Exception:
            pass  # tabela pode não existir ainda — cria na carga abaixo

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            create_disposition="CREATE_IF_NEEDED",
            autodetect=False,
            schema=_dataframe_to_bq_schema(df),
        )
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        logger.info("Silver BQ append: %s %s/%d (%d rows)", table_id, uf.upper(), year, len(df))
        return table_id
    except ImportError:
        logger.warning("google-cloud-bigquery não disponível. Usando local.")
        parts = table_name.rsplit("_", 2)
        if len(parts) == 3:
            return _write_local_silver(df, parts[1], int(parts[2]))
        return _write_local_silver(df, uf, year)


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


_CANDIDATO_NORM_MAP: dict[str, str] = {
    "lula": "LULA",
    "luiz inácio lula da silva": "LULA",
    "luiz inacio lula da silva": "LULA",
    "luiz inacio": "LULA",
    "lula da silva": "LULA",
    "bolsonaro": "BOLSONARO",
    "jair bolsonaro": "BOLSONARO",
    "jair messias bolsonaro": "BOLSONARO",
    "jair messias": "BOLSONARO",
    "ciro": "CIRO GOMES",
    "ciro gomes": "CIRO GOMES",
    "simone tebet": "TEBET",
    "tebet": "TEBET",
    "marina silva": "MARINA",
    "marina": "MARINA",
}


def transform_pesquisas_to_silver(
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze polls (TSE PesqEle + Atlas + Poder360) to Silver.

    Reads:  bronze/pesquisas/{year}/BR/pesquisas_tse_{year}.parquet       (cadastro TSE)
            bronze/pesquisas/{year}/BR/pesquisas_atlas_{year}.parquet     (Atlas Político)
            bronze/pesquisas/{year}/BR/pesquisas_intencao_{year}.parquet  (intenção real)
            bronze/pesquisas/{year}/BR/dim_instituto.parquet
    Writes: Silver table `fact_pesquisa`         — cadastro + intenção enriquecida
            Silver table `fact_pesquisa_intencao` — intenção ajustada por house_effect
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    bronze_dir = LOCAL_BRONZE_DIR / "pesquisas" / str(year) / "BR"
    gcs_prefix = f"raw/pesquisas/{year}/BR"

    def _read_named_parquet(filename: str) -> pd.DataFrame:
        if GCS_BUCKET:
            try:
                from google.cloud import storage as _gcs

                _blob = _gcs.Client().bucket(GCS_BUCKET).blob(f"{gcs_prefix}/{filename}")
                if not _blob.exists():
                    return pd.DataFrame()
                return pd.read_parquet(io.BytesIO(_blob.download_as_bytes()))
            except Exception as exc:
                logger.warning("Falha GCS %s: %s", filename, exc)
                return pd.DataFrame()
        path = bronze_dir / filename
        if not path.exists():
            return pd.DataFrame()
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning("Falha local %s: %s", filename, exc)
            return pd.DataFrame()

    df_tse = _read_named_parquet(f"pesquisas_tse_{year}.parquet")
    df_atlas = _read_named_parquet(f"pesquisas_atlas_{year}.parquet")

    if not df_tse.empty:
        logger.info("Pesquisas Bronze TSE: %d rows (cadastro)", len(df_tse))
    if not df_atlas.empty:
        logger.info("Pesquisas Bronze Atlas: %d rows (intenção de voto)", len(df_atlas))

    # Atlas é fonte primária — enrich com metadata TSE via poll_id
    if not df_atlas.empty and not df_tse.empty:
        _meta = [c for c in ("poll_id", "n_entrevistados", "margem_erro") if c in df_tse.columns]
        if "poll_id" in _meta and len(_meta) > 1:
            tse_meta = df_tse[_meta].dropna(subset=["poll_id"])
            _suffix_map = {c: f"{c}_tse" for c in _meta if c != "poll_id"}
            df_atlas = df_atlas.merge(
                tse_meta.rename(columns=_suffix_map),
                on="poll_id",
                how="left",
            )
            for col, tse_col in _suffix_map.items():
                if col in df_atlas.columns and tse_col in df_atlas.columns:
                    df_atlas[col] = df_atlas[col].fillna(df_atlas[tse_col])
                    df_atlas.drop(columns=[tse_col], inplace=True)
        df_atlas["tipo_registro"] = "intencao_voto"

    frames: list[pd.DataFrame] = []
    if not df_atlas.empty:
        frames.append(df_atlas)

    # Append TSE-only rows (polls not covered by Atlas) as cadastro records
    if not df_tse.empty:
        if not df_atlas.empty and "poll_id" in df_tse.columns and "poll_id" in df_atlas.columns:
            atlas_ids = set(df_atlas["poll_id"].dropna().astype(str))
            df_tse_only = df_tse[~df_tse["poll_id"].astype(str).isin(atlas_ids)].copy()
        else:
            df_tse_only = df_tse.copy()
        if not df_tse_only.empty:
            df_tse_only["tipo_registro"] = "cadastro"
            frames.append(df_tse_only)

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

    # ── fact_pesquisa_intencao — intenção real com candidato normalizado ───────
    intencao_result = _transform_intencao_to_silver(
        year=year,
        bronze_dir=bronze_dir,
        gcs_prefix=gcs_prefix,
        house_map=house_map,
        use_bigquery=use_bigquery,
    )

    return {"status": "ok", "path": path, "rows": len(df), "intencao": intencao_result}


def _transform_intencao_to_silver(
    year: int,
    bronze_dir: "Path",
    gcs_prefix: str,
    house_map: dict[str, float],
    use_bigquery: bool,
) -> dict:
    """Read pesquisas_intencao_{year}.parquet and write fact_pesquisa_intencao Silver."""
    from pathlib import Path as _Path

    def _read_named(filename: str) -> pd.DataFrame:
        if GCS_BUCKET:
            try:
                from google.cloud import storage as _gcs

                blob = _gcs.Client().bucket(GCS_BUCKET).blob(f"{gcs_prefix}/{filename}")
                if not blob.exists():
                    return pd.DataFrame()
                return pd.read_parquet(io.BytesIO(blob.download_as_bytes()))
            except Exception as exc:
                logger.warning("Falha GCS intencao %s: %s", filename, exc)
                return pd.DataFrame()
        path = _Path(bronze_dir) / filename
        if not path.exists():
            return pd.DataFrame()
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning("Falha local intencao %s: %s", filename, exc)
            return pd.DataFrame()

    df_intencao = _read_named(f"pesquisas_intencao_{year}.parquet")

    if df_intencao.empty or "intencao_pct" not in df_intencao.columns:
        logger.info("Bronze pesquisas_intencao_%d: vazio ou sem intencao_pct — pulando", year)
        return {"status": "skipped", "rows": 0}

    df = df_intencao.copy()

    if "candidato" in df.columns:
        df["candidato_normalizado"] = (
            df["candidato"]
            .str.lower()
            .str.strip()
            .map(_CANDIDATO_NORM_MAP)
            .fillna(df["candidato"].str.upper().str.strip())
        )
    else:
        df["candidato_normalizado"] = "DESCONHECIDO"

    if "instituto" in df.columns:
        df["house_effect_score"] = (
            df["instituto"]
            .str.lower()
            .map(lambda x: house_map.get(str(x).strip(), 0.0) if pd.notna(x) else 0.0)
        )
    else:
        df["house_effect_score"] = 0.0

    df["intencao_pct"] = pd.to_numeric(df["intencao_pct"], errors="coerce")
    df["intencao_ajustada"] = df["intencao_pct"] - df["house_effect_score"]

    if "cd_cargo" not in df.columns:
        _cargo_map = {"presidente": 1, "governador": 3, "senador": 5}
        if "cargo" in df.columns:
            df["cd_cargo"] = df["cargo"].str.lower().map(_cargo_map).fillna(1).astype(int)
        else:
            df["cd_cargo"] = 1

    if "uf" not in df.columns:
        df["uf"] = "BR"

    df["ano"] = year
    df["ingested_at"] = pd.Timestamp.utcnow()

    if use_bigquery:
        path = _write_bigquery_intencao(df, year)
    else:
        path_local = LOCAL_SILVER_DIR / f"fact_pesquisa_intencao_{year}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("Pesquisas intencao Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def _write_bigquery_intencao(df: pd.DataFrame, year: int) -> str:
    """Write fact_pesquisa_intencao to BigQuery Silver (pre-delete by year)."""
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    table_id = f"{project}.{dataset}.fact_pesquisa_intencao"

    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    df = _normalize_for_bq(df)
    if "ingested_at" not in df.columns:
        df["ingested_at"] = pd.Timestamp.utcnow()

    try:
        client.query(f"DELETE FROM `{table_id}` WHERE ano = {year}").result()
        logger.info("Pre-delete fact_pesquisa_intencao ano=%d OK", year)
    except Exception:
        pass

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        create_disposition="CREATE_IF_NEEDED",
        autodetect=True,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
    )
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    logger.info("fact_pesquisa_intencao BQ: %s ano=%d (%d rows)", table_id, year, len(df))
    return table_id


import re as _re  # noqa: E402

_TEMA_PATTERNS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"economia|emprego|renda|inflacao|salario|pib", _re.IGNORECASE), "economia"),
    (_re.compile(r"saude|hospital|sus|medico|doenca|covid", _re.IGNORECASE), "saude"),
    (_re.compile(r"educacao|escola|universidade|ensino|professor", _re.IGNORECASE), "educacao"),
    (_re.compile(r"seguranca|violencia|crime|policia|assassinato", _re.IGNORECASE), "seguranca"),
    (_re.compile(r"corrupcao|corrupto|desvio|propina|escandalo", _re.IGNORECASE), "corrupcao"),
    (
        _re.compile(r"meio.ambiente|clima|desmatamento|queimada|ambiental", _re.IGNORECASE),
        "meio_ambiente",
    ),
]


def _extract_temas(text: str) -> list[str]:
    matched = [label for pattern, label in _TEMA_PATTERNS if pattern.search(text)]
    return matched if matched else ["geral"]


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

    # Files excluded from social_mencoes_br: aggregated sentiment and digital trends
    # (handled respectively by dedicated transforms or separate aggregation steps)
    _SOCIAL_EXCLUDE = {"twitter_sentimento", "google_trends_timeline", "google_trends_por_uf"}

    def _is_social_mention_file(name: str) -> bool:
        base = name.split("/")[-1].replace(".parquet", "")
        return not any(base.startswith(excl) for excl in _SOCIAL_EXCLUDE)

    if GCS_BUCKET:
        from google.cloud import storage as _gcs_storage

        prefix = f"raw/social/{year}/BR/"
        try:
            gcs_client = _gcs_storage.Client()
            bucket_obj = gcs_client.bucket(GCS_BUCKET)
            blobs = [
                b
                for b in bucket_obj.list_blobs(prefix=prefix)
                if b.name.endswith(".parquet") and _is_social_mention_file(b.name)
            ]
            if blobs:
                import io as _io

                sub_frames = []
                for blob in blobs:
                    try:
                        sub_frames.append(pd.read_parquet(_io.BytesIO(blob.download_as_bytes())))
                        logger.info("Social Bronze GCS: %s", blob.name.split("/")[-1])
                    except Exception as exc:
                        logger.warning("Falha ao ler GCS social %s: %s", blob.name, exc)
                if sub_frames:
                    frames.append(pd.concat(sub_frames, ignore_index=True))
                    logger.info(
                        "Social Bronze GCS total: %d registros (ano=%d)", len(frames[-1]), year
                    )
        except Exception as exc:
            logger.warning("GCS social read falhou: %s", exc)
    else:
        bronze_social = LOCAL_BRONZE_DIR / "social" / str(year) / "BR"
        # All social mention sources (individual posts/articles, not aggregated files)
        patterns = [
            "twitter_mencoes_*.parquet",
            "facebook_posts_*.parquet",
            "instagram_posts_*.parquet",
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

    # enrich_sentiment_vertex() (v1.2) populates sentimento_score and confianca_nlp;
    # provide safe defaults when Vertex enrichment has not run yet
    if "temas" not in df.columns:
        df["temas"] = df["text"].fillna("").apply(_extract_temas)

    if "sentimento_score" not in df.columns:
        df["sentimento_score"] = 0.0

    if "confianca_nlp" not in df.columns:
        df["confianca_nlp"] = None

    if "suspeito_coordenado" not in df.columns:
        df["suspeito_coordenado"] = False

    if "score_credibilidade_post" not in df.columns:
        source_score = df.get("score_confiabilidade", pd.Series([1.0] * len(df), index=df.index))
        df["score_credibilidade_post"] = source_score * df["suspeito_coordenado"].map(
            {True: 0.3, False: 1.0}
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
        "temas",
        "sentimento_score",
        "confianca_nlp",
        "suspeito_coordenado",
        "score_credibilidade_post",
        "ingested_at",
    ]
    df = df[[c for c in keep if c in df.columns]]

    if use_bigquery:
        project = os.environ.get("GCP_PROJECT_ID", "")
        dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
        table_id = f"{project}.{dataset}.social_mencoes_br"
        from google.cloud import bigquery
        from google.cloud.bigquery import SchemaUpdateOption, WriteDisposition

        from google.api_core.exceptions import NotFound

        client = bigquery.Client(project=project)
        try:
            client.query(
                f"DELETE FROM `{table_id}` WHERE ano = {year}", job_config=bigquery.QueryJobConfig()
            ).result()
        except NotFound:
            pass
        job_config = bigquery.LoadJobConfig(
            write_disposition=WriteDisposition.WRITE_APPEND,
            autodetect=True,
            schema_update_options=[
                SchemaUpdateOption.ALLOW_FIELD_ADDITION,
                SchemaUpdateOption.ALLOW_FIELD_RELAXATION,
            ],
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
        path = _write_bigquery_uf_year(df, "seguranca_municipal", uf, year)
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

    Handles missing columns with intelligent fallbacks:
    - taxa_mortalidade_infantil ← taxa_mortalidade_infantil_1000 or PySUS SIM data
    - pct_cobertura_plano_saude ← ANS beneficiários data
    - Maintains NULL integrity for missing data (no fake defaults)
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

    # Convert all numeric columns (preserves NaN for missing data)
    numeric_cols = [
        "taxa_mortalidade_infantil_1000",
        "taxa_mortalidade_materna_100k",
        "taxa_mortalidade_infantil",
        "pct_cobertura_plano_saude",
        "qt_obitos_total",
        "qt_nascimentos",
        "idsus",
        "tx_mortalidade_infantil",
        "tx_mortalidade_materna",
        "pct_cobertura_esf",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
            "float64"
        )

    # ── Intelligent fallback mapping for common column aliases ────────────────
    # If taxa_mortalidade_infantil is missing but taxa_mortalidade_infantil_1000 exists
    if (
        "taxa_mortalidade_infantil" not in df.columns
        and "taxa_mortalidade_infantil_1000" in df.columns
    ):
        df["taxa_mortalidade_infantil"] = df["taxa_mortalidade_infantil_1000"]
        logger.debug("Mapped taxa_mortalidade_infantil_1000 → taxa_mortalidade_infantil")

    # If tx_* aliases exist, copy to standard ta_* columns
    alias_mappings = {
        "tx_mortalidade_infantil": "taxa_mortalidade_infantil",
        "tx_mortalidade_materna": "taxa_mortalidade_materna",
    }
    for alias_col, std_col in alias_mappings.items():
        if alias_col in df.columns and std_col not in df.columns:
            df[std_col] = df[alias_col]
            logger.debug(f"Mapped {alias_col} → {std_col}")

    # Clean up redundant fontes string column (keep only as metadata comment)
    if "fontes" in df.columns:
        df["fontes"] = df["fontes"].astype(str).str[:500]  # Limit length

    df["ingested_at"] = pd.Timestamp.utcnow()

    table_name = f"saude_municipal_{uf.lower()}_{year}"
    if use_bigquery:
        path = _write_bigquery_uf_year(df, "saude_municipal", uf, year)
    else:
        path_local = LOCAL_SILVER_DIR / f"{table_name}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info(
            "Saúde Silver local: %s (%d rows, colunas: %s)", path, len(df), ", ".join(df.columns)
        )

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
        path = _write_bigquery_uf_year(df, "economia_municipal", uf, year)
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

    numeric_cols = (
        "qtd_beneficiarios_bolsa_familia",
        "valor_total_bolsa_familia_reais",
        "qtd_beneficiarios_novo_bolsa_familia",
        "valor_total_novo_bolsa_familia_reais",
        "qtd_beneficiarios_bpc",
        "valor_total_bpc_reais",
        "qtd_beneficiarios_auxilio_emergencial",
        "valor_total_auxilio_emergencial_reais",
        "qtd_familias_cadunico",
        "qtd_familias_extrema_pobreza",
        "qtd_familias_baixa_renda",
    )
    for col in numeric_cols:
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
            bq.query(
                f"DELETE FROM `{table_id}` WHERE ano = {year}", job_config=bigquery.QueryJobConfig()
            ).result()
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
        id_cols = [c for c in ("date", "ano", "fonte") if c in trends_df.columns]
        val_cols = [c for c in trends_df.columns if c not in id_cols]
        long_df = trends_df.melt(
            id_vars=id_cols, value_vars=val_cols, var_name="candidato", value_name="interesse_busca"
        )
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


def transform_emendas_to_silver(year: int, use_bigquery: bool = False) -> dict:
    """Transform Bronze emendas parlamentares to Silver.

    Reads:  raw/emendas/{year}/BR/emendas_BR_{year}.parquet  (GCS or local)
    Writes: Silver table `emendas_parlamentares` (BQ WRITE_APPEND pre-delete) or local parquet.

    Schema enforced:
      ano, sg_uf, cd_municipio_ibge, nm_municipio,
      nm_parlamentar, sg_partido, sg_uf_parlamentar, ds_cargo_parlamentar,
      tp_emenda, ds_area, ds_subfuncao,
      vl_empenhado, vl_liquidado, vl_pago,
      nr_emenda, fonte, ingested_at
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    # ── Read Bronze ────────────────────────────────────────────────────────────
    df = pd.DataFrame()
    if GCS_BUCKET:
        prefix = f"raw/emendas/{year}/BR/"
        logger.info("Emendas Bronze GCS: prefix=%s", prefix)
        try:
            df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
        except Exception as exc:
            logger.warning("GCS read emendas %d: %s", year, exc)

    if df.empty:
        bronze_path = LOCAL_BRONZE_DIR / "emendas" / str(year) / "BR"
        files = list(bronze_path.glob("*.parquet")) if bronze_path.exists() else []
        if files:
            df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if df.empty:
        return {"status": "error", "message": f"Bronze emendas vazio para ano={year}"}

    # ── Normalize ──────────────────────────────────────────────────────────────
    df = df.copy()
    if "ano" not in df.columns:
        df["ano"] = year

    for col in ("vl_empenhado", "vl_liquidado", "vl_pago"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
            "float64"
        )

    # Truncate IBGE code to 7 digits (some sources send 8)
    if "cd_municipio_ibge" in df.columns:
        df["cd_municipio_ibge"] = df["cd_municipio_ibge"].apply(
            lambda v: int(str(int(v))[:7]) if pd.notna(v) and v > 0 else None
        )

    str_cols = [
        "nm_municipio",
        "sg_uf",
        "nm_parlamentar",
        "sg_partido",
        "sg_uf_parlamentar",
        "ds_cargo_parlamentar",
        "tp_emenda",
        "ds_area",
        "ds_subfuncao",
        "nr_emenda",
        "fonte",
    ]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    if "fonte" not in df.columns or df["fonte"].eq("").all():
        df["fonte"] = "portal_transparencia_emendas"

    df["score_confiabilidade"] = 9.0  # Portal da Transparência — dado oficial federal
    df["ingested_at"] = pd.Timestamp.utcnow()

    logger.info(
        "Emendas Silver %d: %d registros | %d UFs | R$ %.1fM pago",
        year,
        len(df),
        df["sg_uf"].nunique() if "sg_uf" in df.columns else 0,
        df["vl_pago"].sum() / 1e6 if "vl_pago" in df.columns else 0,
    )

    # ── Write ──────────────────────────────────────────────────────────────────
    if use_bigquery:
        project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
        dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
        try:
            from google.cloud import bigquery
            from google.cloud.bigquery import SchemaUpdateOption, WriteDisposition

            client = bigquery.Client(project=project)
            table_id = f"{project}.{dataset}.emendas_parlamentares"
            df_bq = _normalize_for_bq(df)

            try:
                client.query(f"DELETE FROM `{table_id}` WHERE ano = {year}").result()
                logger.info("Emendas Silver pre-delete ano=%d OK", year)
            except Exception:
                pass

            job_config = bigquery.LoadJobConfig(
                write_disposition=WriteDisposition.WRITE_APPEND,
                create_disposition="CREATE_IF_NEEDED",
                autodetect=True,
                schema_update_options=[SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            )
            client.load_table_from_dataframe(df_bq, table_id, job_config=job_config).result()
            logger.info("Emendas Silver BQ: %s (%d rows)", table_id, len(df))
            return {"status": "ok", "path": table_id, "rows": len(df)}
        except ImportError:
            logger.warning("google-cloud-bigquery não disponível. Usando local.")

    path_local = LOCAL_SILVER_DIR / f"emendas_parlamentares_{year}.parquet"
    df.to_parquet(path_local, index=False, compression="zstd")
    logger.info("Emendas Silver local: %s (%d rows)", path_local, len(df))
    return {"status": "ok", "path": str(path_local), "rows": len(df)}


def transform_sancoes_to_silver(use_bigquery: bool = False) -> dict:
    """Transform Bronze CEIS + CNEP sanções to Silver.

    Reads:  raw/sancoes/snapshot/BR/ceis_BR_snapshot.parquet  (GCS or local)
            raw/sancoes/snapshot/BR/cnep_BR_snapshot.parquet
    Writes: Silver table `sancoes_empresas` (BQ WRITE_TRUNCATE) or local parquet.

    Schema enforced:
      fonte_sistema (CEIS|CNEP|CEAF|CEPIM), nm_pessoa, tp_pessoa, nr_cpf_cnpj (mascarado),
      nm_sancionador, tp_sancao, dt_inicio_sancao, dt_fim_sancao,
      nm_orgao_sancionador, sg_uf_sancionador, nm_municipio_sancionador,
      valor_multa, nm_cargo (CEAF), score_confiabilidade, ingested_at
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []

    _sancoes_year = os.environ.get("SANCOES_YEAR", "2026")

    def _read_sancoes_bronze(sistema: str) -> pd.DataFrame:
        # Bronze writer stores at raw/sancoes/{year}/BR/ (year = snapshot year, e.g. 2026)
        prefix = f"raw/sancoes/{_sancoes_year}/BR/{sistema.lower()}_BR_snapshot.parquet"
        if GCS_BUCKET:
            try:
                import io
                from google.cloud import storage

                client = storage.Client()
                blob = client.bucket(GCS_BUCKET).blob(prefix)
                if blob.exists():
                    return pd.read_parquet(io.BytesIO(blob.download_as_bytes()))
            except Exception as exc:
                logger.warning("GCS read %s: %s", prefix, exc)
        local = (
            LOCAL_BRONZE_DIR
            / "sancoes"
            / _sancoes_year
            / "BR"
            / f"{sistema.lower()}_BR_snapshot.parquet"
        )
        if local.exists():
            return pd.read_parquet(local)
        return pd.DataFrame()

    for sistema in ("ceis", "cnep", "ceaf", "cepim"):
        df_s = _read_sancoes_bronze(sistema)
        if not df_s.empty:
            df_s["fonte_sistema"] = sistema.upper()
            frames.append(df_s)
            logger.info("Sanções Bronze %s: %d registros", sistema.upper(), len(df_s))

    if not frames:
        return {"status": "error", "message": "Bronze sanções vazio (CEIS + CNEP + CEAF + CEPIM)"}

    df = pd.concat(frames, ignore_index=True)
    df = df.copy()

    # ── Normalize field names (Portal da Transparência schema) ────────────────
    _rename = {
        "nomeInfrator": "nm_pessoa",
        "tipoPessoa": "tp_pessoa",
        "cpfCnpj": "nr_cpf_cnpj",
        "nomeSancionado": "nm_pessoa",
        "cpfCnpjSancionado": "nr_cpf_cnpj",
        "tipoSancao": "tp_sancao",
        "dataInicioSancao": "dt_inicio_sancao",
        "dataFimSancao": "dt_fim_sancao",
        "orgaoSancionador": "nm_orgao_sancionador",
        "ufOrgaoSancionador": "sg_uf_sancionador",
        "municipioSancionador": "nm_municipio_sancionador",
        "valorMulta": "valor_multa",
        "nomeFantasia": "nm_fantasia",
        "razaoSocial": "nm_razao_social",
        "fundamentacaoLegal": "ds_fundamentacao",
    }
    df = df.rename(columns={k: v for k, v in _rename.items() if k in df.columns})

    # Ensure canonical columns exist (nm_cargo e nm_orgao_lotacao são específicos do CEAF)
    for col in (
        "nm_pessoa",
        "tp_pessoa",
        "nr_cpf_cnpj",
        "tp_sancao",
        "dt_inicio_sancao",
        "dt_fim_sancao",
        "nm_orgao_sancionador",
        "sg_uf_sancionador",
        "nm_municipio_sancionador",
        "nm_cargo",
        "nm_orgao_lotacao",
    ):
        if col not in df.columns:
            df[col] = ""

    # Parse dates
    for col in ("dt_inicio_sancao", "dt_fim_sancao"):
        df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)

    # Mask CPF/CNPJ — keep only last 4 digits visible
    if "nr_cpf_cnpj" in df.columns:

        def _mask_doc(v: str) -> str:
            v = str(v).strip() if pd.notna(v) else ""
            digits = "".join(c for c in v if c.isdigit())
            if len(digits) >= 4:
                return "*" * (len(digits) - 4) + digits[-4:]
            return "****"

        df["nr_cpf_cnpj"] = df["nr_cpf_cnpj"].apply(_mask_doc)

    # Numeric
    if "valor_multa" in df.columns:
        df["valor_multa"] = pd.to_numeric(df["valor_multa"], errors="coerce").fillna(0.0)

    # String clean
    str_cols = [
        "nm_pessoa",
        "tp_pessoa",
        "tp_sancao",
        "nm_orgao_sancionador",
        "sg_uf_sancionador",
        "nm_municipio_sancionador",
        "fonte_sistema",
    ]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    if "fonte" not in df.columns:
        df["fonte"] = "portal_transparencia_sancoes"
    df["score_confiabilidade"] = 9.5  # cadastros federais oficiais
    df["ingested_at"] = pd.Timestamp.utcnow()

    logger.info(
        "Sanções Silver: %d registros | CEIS=%d | CNEP=%d | CEAF=%d | CEPIM=%d",
        len(df),
        (df["fonte_sistema"] == "CEIS").sum(),
        (df["fonte_sistema"] == "CNEP").sum(),
        (df["fonte_sistema"] == "CEAF").sum(),
        (df["fonte_sistema"] == "CEPIM").sum(),
    )

    # ── Write ──────────────────────────────────────────────────────────────────
    if use_bigquery:
        project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
        dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
        try:
            from google.cloud import bigquery
            from google.cloud.bigquery import WriteDisposition

            client = bigquery.Client(project=project)
            table_id = f"{project}.{dataset}.sancoes_empresas"
            df_bq = _normalize_for_bq(df)

            job_config = bigquery.LoadJobConfig(
                write_disposition=WriteDisposition.WRITE_TRUNCATE,
                create_disposition="CREATE_IF_NEEDED",
                autodetect=True,
            )
            client.load_table_from_dataframe(df_bq, table_id, job_config=job_config).result()
            logger.info("Sanções Silver BQ: %s (%d rows)", table_id, len(df))
            return {"status": "ok", "path": table_id, "rows": len(df)}
        except ImportError:
            logger.warning("google-cloud-bigquery não disponível. Usando local.")

    path_local = LOCAL_SILVER_DIR / "sancoes_empresas_snapshot.parquet"
    df.to_parquet(path_local, index=False, compression="zstd")
    logger.info("Sanções Silver local: %s (%d rows)", path_local, len(df))
    return {"status": "ok", "path": str(path_local), "rows": len(df)}


def transform_candidaturas_to_silver(
    years: list[int] | None = None,
    use_bigquery: bool = False,
) -> dict:
    """Load Bronze tse_candidaturas → Silver dim_candidato (partido lookup table).

    Reads all UF parquet files for the given years from GCS and writes a
    deduplicated dim_candidato to spepe_silver.dim_candidato.
    Key columns: sq_candidato, nr_candidato, sg_uf, sg_partido, nm_partido,
                 nm_candidato, nm_urna, cd_cargo, ds_cargo, ano.
    Called by silver_transform_job and used by gold_builder to populate sg_partido.
    """
    import io as _io

    _years = years or [2018, 2022]
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    bucket = GCS_BUCKET

    _COLS = [
        "sq_candidato",
        "nr_candidato",
        "sg_uf",
        "sg_partido",
        "nm_partido",
        "nm_candidato",
        "nm_urna",
        "cd_cargo",
        "ds_cargo",
        "ano",
    ]

    if not bucket:
        return {"status": "skipped", "message": "GCS_BUCKET não configurado"}

    try:
        from google.cloud import storage as _gcs
    except ImportError:
        return {"status": "skipped", "message": "google-cloud-storage não disponível"}

    gcs_client = _gcs.Client()
    bucket_obj = gcs_client.bucket(bucket)

    frames: list[pd.DataFrame] = []
    for year in _years:
        prefix = f"raw/tse_candidaturas/{year}/"
        blobs = list(bucket_obj.list_blobs(prefix=prefix))
        for blob in blobs:
            if not blob.name.endswith(".parquet"):
                continue
            try:
                raw = blob.download_as_bytes()
                df_blob = pd.read_parquet(_io.BytesIO(raw))
                available = [c for c in _COLS if c in df_blob.columns]
                df_blob = df_blob[available].copy()
                if "ano" not in df_blob.columns:
                    df_blob["ano"] = year
                frames.append(df_blob)
            except Exception as exc:
                logger.warning("Candidaturas parquet falhou %s: %s", blob.name, exc)

    if not frames:
        return {"status": "skipped", "message": "Bronze tse_candidaturas vazio no GCS"}

    df_all = pd.concat(frames, ignore_index=True)
    if "sq_candidato" in df_all.columns and "ano" in df_all.columns:
        df_all = df_all.drop_duplicates(subset=["sq_candidato", "ano"])

    if not use_bigquery or not project:
        out = LOCAL_SILVER_DIR / "dim_candidato.parquet"
        LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)
        df_all.to_parquet(out, index=False)
        return {"status": "ok", "rows": len(df_all), "path": str(out)}

    table_id = f"{project}.{dataset}.dim_candidato"
    try:
        from google.cloud import bigquery as _bq

        bq_client = _bq.Client(project=project)
        job_config = _bq.LoadJobConfig(
            write_disposition=_bq.WriteDisposition.WRITE_TRUNCATE,
            create_disposition="CREATE_IF_NEEDED",
            autodetect=True,
        )
        bq_client.load_table_from_dataframe(df_all, table_id, job_config=job_config).result()
        logger.info("dim_candidato Silver: %d rows → %s", len(df_all), table_id)
        return {"status": "ok", "rows": len(df_all), "table": table_id}
    except Exception as exc:
        logger.error("dim_candidato Silver BQ falhou: %s", exc)
        return {"status": "error", "message": str(exc)}


def transform_endividamento_to_silver(
    year_start: int,
    year_end: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze BCB endividamento → Silver endividamento_nacional (national time series).

    Reads:  raw/endividamento/{year_end}/{UF}/endividamento_*_{year_start}_{year_end}.parquet
    Writes: Silver table `endividamento_nacional` deduplicated by data_referencia.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    if GCS_BUCKET:
        prefix = f"raw/endividamento/{year_end}/"
        try:
            df_gcs = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
            if not df_gcs.empty:
                frames.append(df_gcs)
                logger.info("Endividamento Bronze GCS: %d rows (prefix=%s)", len(df_gcs), prefix)
        except Exception as exc:
            logger.warning("GCS endividamento read: %s", exc)

    if not frames:
        bronze_path = LOCAL_BRONZE_DIR / "endividamento" / str(year_end)
        for f in (
            bronze_path.rglob(f"endividamento_*_{year_start}_{year_end}.parquet")
            if bronze_path.exists()
            else []
        ):
            try:
                frames.append(pd.read_parquet(f))
            except Exception as exc:
                logger.warning("Endividamento local read %s: %s", f, exc)

    if not frames:
        return {
            "status": "error",
            "message": f"Bronze endividamento vazio para {year_start}-{year_end}",
        }

    df = pd.concat(frames, ignore_index=True)

    # Deduplicate — BACEN data is national, same value propagated per municipality
    # Keep only unique national time series rows (by data_referencia)
    ts_cols = [
        "data_referencia",
        "ano",
        "mes",
        "endividamento_familias_pct",
        "comprometimento_renda_pct",
        "inadimplencia_pf_pct",
        "inadimplencia_pf_credito",
        "fontes",
        "granularidade",
    ]
    avail = [c for c in ts_cols if c in df.columns]
    if "data_referencia" in avail:
        df = df[avail].drop_duplicates(subset=["data_referencia"])
    else:
        df = df[avail]

    for col in (
        "endividamento_familias_pct",
        "comprometimento_renda_pct",
        "inadimplencia_pf_pct",
        "inadimplencia_pf_credito",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("Endividamento Silver: %d períodos mensais", len(df))

    if use_bigquery:
        project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
        dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
        try:
            from google.cloud import bigquery
            from google.cloud.bigquery import WriteDisposition

            client = bigquery.Client(project=project)
            table_id = f"{project}.{dataset}.endividamento_nacional"
            df_bq = _normalize_for_bq(df)
            try:
                client.query(f"TRUNCATE TABLE `{table_id}`").result()
            except Exception:
                pass
            job_config = bigquery.LoadJobConfig(
                write_disposition=WriteDisposition.WRITE_APPEND,
                create_disposition="CREATE_IF_NEEDED",
                autodetect=True,
            )
            client.load_table_from_dataframe(df_bq, table_id, job_config=job_config).result()
            logger.info("Endividamento Silver BQ: %s (%d rows)", table_id, len(df))
            return {"status": "ok", "path": table_id, "rows": len(df)}
        except ImportError:
            pass

    path_local = LOCAL_SILVER_DIR / f"endividamento_nacional_{year_start}_{year_end}.parquet"
    df.to_parquet(path_local, index=False, compression="zstd")
    logger.info("Endividamento Silver local: %s (%d rows)", path_local, len(df))
    return {"status": "ok", "path": str(path_local), "rows": len(df)}


def transform_camara_senado_to_silver(
    years: list[int] | None = None,
    legislature: int = 57,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze câmara/senado → Silver votacoes_parlamentares + parlamentares_federais.

    Reads:  raw/camara_senado/{year}/BR/votacoes_camara_{year}.parquet
            raw/camara_senado/{year}/BR/votacoes_senado_{year}.parquet
            raw/camara_senado/{year}/BR/parlamentares_leg{legislature}.parquet
    Writes: Silver table `votacoes_parlamentares` (WRITE_APPEND pre-delete by ano)
            Silver table `parlamentares_federais` (WRITE_TRUNCATE)
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)
    _years = years or [2023, 2024, 2025]
    use_bq = use_bigquery
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
    results: dict[str, dict] = {}

    def _read_bronze(source_year: int | None, filename: str) -> pd.DataFrame:
        yr = source_year or _years[-1]
        if GCS_BUCKET:
            prefix = f"raw/camara_senado/{yr}/BR/{filename}"
            try:
                from google.cloud import storage as _gcs
                import io as _io

                blob = _gcs.Client().bucket(GCS_BUCKET).blob(prefix)
                if blob.exists():
                    return pd.read_parquet(_io.BytesIO(blob.download_as_bytes()))
            except Exception as exc:
                logger.warning("GCS camara_senado read %s: %s", prefix, exc)
        local = LOCAL_BRONZE_DIR / "camara_senado" / str(yr) / "BR" / filename
        if local.exists():
            return pd.read_parquet(local)
        return pd.DataFrame()

    # ── Parlamentares (base) ──────────────────────────────────────────────────
    df_parl = _read_bronze(None, f"parlamentares_leg{legislature}.parquet")
    if not df_parl.empty:
        df_parl["ingested_at"] = pd.Timestamp.utcnow()
        if use_bq:
            try:
                from google.cloud import bigquery

                client = bigquery.Client(project=project)
                table_id = f"{project}.{dataset}.parlamentares_federais"
                df_bq = _normalize_for_bq(df_parl)
                cfg = bigquery.LoadJobConfig(
                    write_disposition="WRITE_TRUNCATE",
                    create_disposition="CREATE_IF_NEEDED",
                    autodetect=True,
                )
                client.load_table_from_dataframe(df_bq, table_id, job_config=cfg).result()
                results["parlamentares_federais"] = {"path": table_id, "rows": len(df_parl)}
                logger.info("Parlamentares Silver BQ: %d rows", len(df_parl))
            except Exception as exc:
                logger.warning("Parlamentares Silver BQ falhou: %s", exc)
        else:
            p = LOCAL_SILVER_DIR / "parlamentares_federais.parquet"
            df_parl.to_parquet(p, index=False, compression="zstd")
            results["parlamentares_federais"] = {"path": str(p), "rows": len(df_parl)}

    # ── Votações (câmara + senado por ano) ────────────────────────────────────
    vot_frames: list[pd.DataFrame] = []
    for year in _years:
        for casa, filename in (
            ("Câmara", f"votacoes_camara_{year}.parquet"),
            ("Senado", f"votacoes_senado_{year}.parquet"),
        ):
            df_v = _read_bronze(year, filename)
            if df_v.empty:
                continue
            if "casa" not in df_v.columns:
                df_v["casa"] = casa
            if "ano" not in df_v.columns:
                df_v["ano"] = year
            if "mes" not in df_v.columns and "data_votacao" in df_v.columns:
                df_v["mes"] = pd.to_datetime(df_v["data_votacao"], errors="coerce").dt.month
            vot_frames.append(df_v)

    if vot_frames:
        df_vot = pd.concat(vot_frames, ignore_index=True)
        df_vot["ingested_at"] = pd.Timestamp.utcnow()
        for col in ("sg_partido", "sg_uf", "tema", "voto", "casa"):
            if col in df_vot.columns:
                df_vot[col] = df_vot[col].fillna("").astype(str).str.strip()
        if use_bq:
            try:
                from google.cloud import bigquery

                client = bigquery.Client(project=project)
                table_id = f"{project}.{dataset}.votacoes_parlamentares"
                df_bq = _normalize_for_bq(df_vot)
                for year in _years:
                    try:
                        client.query(f"DELETE FROM `{table_id}` WHERE ano = {year}").result()
                    except Exception:
                        pass
                cfg = bigquery.LoadJobConfig(
                    write_disposition="WRITE_APPEND",
                    create_disposition="CREATE_IF_NEEDED",
                    autodetect=True,
                    schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
                )
                client.load_table_from_dataframe(df_bq, table_id, job_config=cfg).result()
                results["votacoes_parlamentares"] = {"path": table_id, "rows": len(df_vot)}
                logger.info("Votações Parlamentares Silver BQ: %d rows", len(df_vot))
            except Exception as exc:
                logger.warning("Votações Silver BQ falhou: %s", exc)
        else:
            p = LOCAL_SILVER_DIR / "votacoes_parlamentares.parquet"
            df_vot.to_parquet(p, index=False, compression="zstd")
            results["votacoes_parlamentares"] = {"path": str(p), "rows": len(df_vot)}

    if not results:
        return {"status": "error", "message": "Bronze câmara/senado vazio"}
    return {"status": "ok", "tables": results}


def transform_tse_perfil_to_silver(
    uf: str,
    year: int,
    use_bigquery: bool = False,
) -> dict:
    """Transform Bronze TSE Perfil Eleitorado → Silver perfil_eleitorado.

    Reads:  raw/tse_perfil/{year}/{UF}/perfil_{UF}_{year}.parquet
    Writes: Silver table `perfil_eleitorado` (WRITE_APPEND pre-delete by sg_uf + ano)
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame()
    if GCS_BUCKET:
        prefix = f"raw/tse_perfil/{year}/{uf.upper()}/"
        try:
            df = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
        except Exception as exc:
            logger.warning("GCS tse_perfil read %s/%d: %s", uf, year, exc)

    if df.empty:
        bronze_path = LOCAL_BRONZE_DIR / "tse_perfil" / str(year) / uf.upper()
        files = (
            list(bronze_path.glob(f"perfil_{uf.upper()}_{year}.parquet"))
            if bronze_path.exists()
            else []
        )
        if files:
            df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if df.empty:
        return {"status": "error", "message": f"Bronze TSE perfil vazio para {uf}/{year}"}

    df = df.copy()
    if "sg_uf" not in df.columns:
        df["sg_uf"] = uf.upper()
    if "ano" not in df.columns:
        df["ano"] = year

    for col in ("qt_eleitores", "qt_eleitores_deficiencia", "qt_eleitores_biometria"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("Int64")

    str_cols = (
        "ds_genero",
        "ds_faixa_etaria",
        "ds_grau_escolaridade",
        "ds_estado_civil",
        "nm_municipio",
        "sg_uf",
    )
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("TSE Perfil Silver %s/%d: %d rows", uf.upper(), year, len(df))

    if use_bigquery:
        path = _write_bigquery_uf_year(df, "perfil_eleitorado", uf, year)
    else:
        path_local = LOCAL_SILVER_DIR / f"perfil_eleitorado_{uf.lower()}_{year}.parquet"
        df.to_parquet(path_local, index=False, compression="zstd")
        path = str(path_local)
        logger.info("TSE Perfil Silver local: %s (%d rows)", path, len(df))

    return {"status": "ok", "path": path, "rows": len(df)}


def transform_presidente_to_silver(year: int, use_bigquery: bool = False) -> dict:
    """Transform Bronze TSE Presidente (nacional) → Silver (expandido para UFs).

    Reads: raw/tse_presidente/{year}/BR/presidente_{year}.parquet
    Writes: Silver table `tse_presidente_{year}` (nacional expandido por UF)

    Estratégia: Presidente é cargo nacional (BR).
    Expandir para UFs proporcionalmente aos votos.
    """
    LOCAL_SILVER_DIR.mkdir(parents=True, exist_ok=True)

    df_pres = pd.DataFrame()

    # 1. Ler Bronze presidente (nacional BR)
    if GCS_BUCKET:
        prefix = f"raw/tse_presidente/{year}/BR/"
        try:
            df_pres = _read_gcs_parquet_glob(GCS_BUCKET, prefix)
        except Exception as exc:
            logger.warning("GCS tse_presidente read %d: %s", year, exc)

    if df_pres.empty:
        bronze_path = LOCAL_BRONZE_DIR / "tse_presidente" / str(year) / "BR"
        files = list(bronze_path.glob(f"presidente_{year}.parquet")) if bronze_path.exists() else []
        if files:
            df_pres = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if df_pres.empty:
        logger.warning("Bronze tse_presidente vazio para %d", year)
        return {"status": "error", "message": f"Bronze TSE presidente vazio para {year}"}

    df_pres = df_pres.copy()

    # 2. Normalizar colunas para schema Silver (TSE)
    df_pres = _normalize_tse(df_pres, year)

    # 3. Expandir presidente nacional para UFs (replicar para cada UF)
    all_ufs = [
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    ]
    df_pres_expandido = []
    for uf in all_ufs:
        df_temp = df_pres.copy()
        df_temp["sg_uf"] = uf
        # Dividir votos proporcionalmente por UF
        num_ufs = len(all_ufs)
        if "qt_votos" in df_temp.columns:
            df_temp["qt_votos"] = (df_temp["qt_votos"] / num_ufs).astype(int)
        df_pres_expandido.append(df_temp)

    df_pres = pd.concat(df_pres_expandido, ignore_index=True)

    # 4. Garantir colunas mínimas de Silver
    required_cols = [
        "sg_uf",
        "cd_municipio",
        "nm_municipio",
        "nr_zona",
        "nr_secao",
        "nm_candidato",
        "qt_votos",
        "ds_cargo",
        "cd_cargo",
        "ano_eleicao",
    ]
    for col in required_cols:
        if col not in df_pres.columns:
            if col in ("cd_municipio", "nr_zona", "nr_secao"):
                df_pres[col] = 0
            elif col == "nm_municipio":
                df_pres[col] = ""
            elif col == "ano_eleicao":
                df_pres[col] = year
            else:
                df_pres[col] = ""

    df_pres = df_pres[required_cols]

    # 5. Salvar em Silver (local apenas, sem BQ para simplificar)
    path_local = LOCAL_SILVER_DIR / f"tse_presidente_{year}.parquet"
    df_pres.to_parquet(path_local, index=False, compression="zstd")
    path = str(path_local)
    logger.info("TSE Presidente Silver local: %s (%d rows)", path, len(df_pres))

    return {"status": "ok", "path": path, "rows": len(df_pres)}


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
