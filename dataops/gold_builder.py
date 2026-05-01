"""Gold layer builder: Silver → 3 Gold tables (~200 features each)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.dataops.gold")

LOCAL_GOLD_DIR = Path(os.environ.get("DATA_DIR", "data")) / "gold"
LOCAL_SILVER_DIR = Path(os.environ.get("DATA_DIR", "data")) / "silver"
_BQ_SILVER_DATASET = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "")


def _load_silver_from_bigquery() -> pd.DataFrame:
    """Read all tse_* tables from BigQuery Silver dataset."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=_GCP_PROJECT)
        tables = list(client.list_tables(f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}"))
        tse_tables = [t for t in tables if t.table_id.startswith("tse_")]
        if not tse_tables:
            return pd.DataFrame()
        frames = []
        for t in tse_tables:
            table_ref = f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}.{t.table_id}"
            df = client.query(f"SELECT * FROM `{table_ref}`").to_dataframe(
                create_bqstorage_client=False
            )
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception as exc:
        logger.warning("BigQuery Silver read failed: %s", exc)
        return pd.DataFrame()


ELECTION_YEARS = [2014, 2018, 2022]


def build_gold(use_bigquery: bool = False) -> dict:
    """Build all 3 Gold tables from Silver layer."""
    LOCAL_GOLD_DIR.mkdir(parents=True, exist_ok=True)

    df_all = pd.DataFrame()
    if use_bigquery and _GCP_PROJECT:
        df_all = _load_silver_from_bigquery()

    if df_all.empty:
        silver_files = list(LOCAL_SILVER_DIR.glob("tse_*.parquet"))
        if not silver_files:
            return {
                "status": "error",
                "message": "Nenhum arquivo Silver disponível. Execute silver_transform primeiro.",
            }
        df_all = pd.concat([pd.read_parquet(f) for f in silver_files], ignore_index=True)

    result = {}

    fact_mun = _build_fact_municipio_eleicao(df_all)
    result["fact_municipio_eleicao"] = _write_gold(fact_mun, "fact_municipio_eleicao", use_bigquery)

    fact_sec = _build_fact_secao_eleicao(df_all)
    result["fact_secao_eleicao"] = _write_gold(fact_sec, "fact_secao_eleicao", use_bigquery)

    fact_cand = _build_fact_candidato_dia(df_all)
    result["fact_candidato_dia"] = _write_gold(fact_cand, "fact_candidato_dia", use_bigquery)

    ibge_data = _load_ibge_silver()
    fact_pesq = _build_fact_pesquisa(ibge_data)
    result["fact_pesquisa"] = _write_gold(fact_pesq, "fact_pesquisa", use_bigquery)

    return {"status": "ok", "tables": result}


def _build_fact_municipio_eleicao(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate TSE + IBGE data per municipality × election into ~200 features.

    Uses `cod_municipio_ibge` (7-digit IBGE code) as the global municipality key,
    falling back to `cd_municipio` (TSE code) if IBGE code not yet joined.
    """
    if df.empty:
        return pd.DataFrame()

    # Convert Arrow strings to regular strings to avoid pandas dtype issues
    df = df.copy()
    for col in df.columns:
        if hasattr(df[col].dtype, "name") and "string" in str(df[col].dtype):
            df[col] = df[col].astype(str)

    preferred_keys = ["cod_municipio_ibge", "cd_municipio"]
    municipality_key = next((k for k in preferred_keys if k in df.columns), None)
    if municipality_key is None:
        logger.warning("fact_municipio_eleicao: no municipality key found in Silver data")
        return pd.DataFrame()

    group_cols = [municipality_key, "sg_uf", "ano_eleicao"]
    if "cd_cargo" in df.columns:
        group_cols.append("cd_cargo")
    if "nr_turno" in df.columns:
        group_cols.append("nr_turno")

    agg_cols = {}

    if "qt_votos" in df.columns and "nm_candidato" in df.columns:
        candidates = df["nm_candidato"].value_counts().head(10).index.tolist()
        for cand in candidates:
            col_name = f"qt_votos_{cand[:20].replace(' ', '_').lower()}"
            df[col_name] = df["qt_votos"].where(df["nm_candidato"] == cand, 0)
            agg_cols[col_name] = "sum"

        agg_cols["qt_votos"] = "sum"

    ibge_cols = [
        c
        for c in df.columns
        if any(ind in c for ind in ["idhm", "renda", "estudo", "populacao", "ibge"])
    ]
    for col in ibge_cols:
        if col in df.columns:
            agg_cols[col] = "first"

    if not agg_cols:
        return df

    avail_group = [c for c in group_cols if c in df.columns]
    avail_agg = {k: v for k, v in agg_cols.items() if k in df.columns}

    if not avail_group or not avail_agg:
        return df

    # Use as_index=False to keep groupby columns as regular columns (avoid index conflicts)
    fact = df.groupby(avail_group, as_index=False).agg(avail_agg)
    logger.info(f"fact_municipio_eleicao: {len(fact)} rows, {len(fact.columns)} colunas")
    return fact


def _build_fact_candidato_dia(df: pd.DataFrame) -> pd.DataFrame:
    """Build candidate × day time series (placeholder from static data)."""
    if df.empty:
        return pd.DataFrame()

    if "nm_candidato" not in df.columns:
        return pd.DataFrame()

    cand_agg = (
        df.groupby(
            ["nm_candidato", "ano_eleicao"] if "ano_eleicao" in df.columns else ["nm_candidato"]
        )
        .agg(
            qt_votos_total=(
                ("qt_votos", "sum") if "qt_votos" in df.columns else ("nm_candidato", "count")
            ),
        )
        .reset_index()
    )

    logger.info(f"fact_candidato_dia: {len(cand_agg)} candidatos")
    return cand_agg


def _load_ibge_silver() -> pd.DataFrame:
    ibge_files = list(LOCAL_SILVER_DIR.glob("ibge_*.parquet"))
    if not ibge_files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in ibge_files], ignore_index=True)


def _build_fact_pesquisa(df_ibge: pd.DataFrame) -> pd.DataFrame:
    """Build survey aggregation table (placeholder — populated by pesquisas MCP)."""
    pesquisa_files = list((Path("data/bronze/pesquisas")).glob("*.parquet"))
    if not pesquisa_files:
        return pd.DataFrame(
            columns=[
                "data_pesquisa",
                "instituto",
                "candidato",
                "intencao_pct",
                "house_effect",
                "intencao_ajustada",
            ]
        )

    dfs = [pd.read_parquet(f) for f in pesquisa_files]
    return pd.concat(dfs, ignore_index=True)


def _write_gold(df: pd.DataFrame, table_name: str, use_bigquery: bool) -> str:
    if df.empty:
        logger.warning(f"Gold {table_name}: DataFrame vazio, não escrito.")
        return ""

    if use_bigquery:
        return _write_bigquery_gold(df, table_name)

    path = LOCAL_GOLD_DIR / f"{table_name}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    logger.info(f"Gold escrito: {path} ({len(df)} rows, {len(df.columns)} colunas)")
    return str(path)


_GOLD_PARTITION_FIELD = {
    "fact_municipio_eleicao": "ano_eleicao",
    "fact_secao_eleicao": "ano_eleicao",
    "fact_candidato_dia": "data",
    "fact_pesquisa": "data_pesquisa",
}

_GOLD_CLUSTER_FIELDS = {
    "fact_municipio_eleicao": ["sg_uf", "cod_municipio_ibge", "ano_eleicao"],
    "fact_secao_eleicao": ["sg_uf", "cod_municipio_ibge", "nr_zona"],
    "fact_candidato_dia": ["nm_candidato", "sg_uf", "ano_eleicao"],
    "fact_pesquisa": ["instituto", "candidato", "sg_uf"],
}


def _normalize_for_bq(df: pd.DataFrame) -> pd.DataFrame:
    """Convert pandas extension types (Int64, Float64, boolean) to numpy types for BQ upload."""
    df = df.copy()
    for col in df.columns:
        dtype = df[col].dtype
        if hasattr(dtype, "numpy_dtype"):
            if pd.api.types.is_integer_dtype(dtype):
                df[col] = df[col].astype("float64")
            elif pd.api.types.is_float_dtype(dtype):
                df[col] = df[col].astype("float64")
            elif pd.api.types.is_bool_dtype(dtype):
                df[col] = df[col].astype("object")
        elif hasattr(df[col], "cat"):
            df[col] = df[col].astype("object")
    return df


def _write_bigquery_gold(df: pd.DataFrame, table_name: str) -> str:
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.{table_name}"

        partition_field = _GOLD_PARTITION_FIELD.get(table_name)
        cluster_fields = _GOLD_CLUSTER_FIELDS.get(table_name)

        df = _normalize_for_bq(df)

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            create_disposition="CREATE_IF_NEEDED",
            time_partitioning=(
                bigquery.TimePartitioning(field=partition_field) if partition_field else None
            ),
            clustering_fields=(
                [f for f in cluster_fields if f in df.columns] if cluster_fields else None
            ),
            autodetect=False,
            schema=_dataframe_to_bq_schema(df),
        )

        if "ingested_at" not in df.columns:
            df = df.copy()
            df["ingested_at"] = pd.Timestamp.utcnow()

        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        logger.info("Gold BigQuery: %s (%d rows)", table_id, len(df))
        return table_id
    except ImportError:
        logger.warning("BigQuery não disponível. Usando local.")
        return _write_gold(df, table_name, use_bigquery=False)


def _dataframe_to_bq_schema(df: pd.DataFrame) -> list:
    from google.cloud import bigquery

    _type_map = {
        "int64": "INT64",
        "int32": "INT64",
        "Int64": "FLOAT64",
        "Int32": "FLOAT64",
        "float64": "FLOAT64",
        "float32": "FLOAT64",
        "Float64": "FLOAT64",
        "Float32": "FLOAT64",
        "bool": "BOOL",
        "boolean": "BOOL",
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


UF_REGIAO = {
    "AC": "Norte",
    "AM": "Norte",
    "AP": "Norte",
    "PA": "Norte",
    "RO": "Norte",
    "RR": "Norte",
    "TO": "Norte",
    "AL": "Nordeste",
    "BA": "Nordeste",
    "CE": "Nordeste",
    "MA": "Nordeste",
    "PB": "Nordeste",
    "PE": "Nordeste",
    "PI": "Nordeste",
    "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste",
    "GO": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "MT": "Centro-Oeste",
    "ES": "Sudeste",
    "MG": "Sudeste",
    "RJ": "Sudeste",
    "SP": "Sudeste",
    "PR": "Sul",
    "RS": "Sul",
    "SC": "Sul",
}


def _build_fact_secao_eleicao(df: pd.DataFrame) -> pd.DataFrame:
    """Tabela granular — mantém nr_zona e nr_secao sem agregar."""
    if df.empty:
        return pd.DataFrame()
    required = [
        "sg_uf",
        "cd_municipio",
        "nr_zona",
        "nr_secao",
        "nm_candidato",
        "sg_partido",
        "cd_cargo",
        "nr_turno",
        "qt_votos",
        "ano_eleicao",
    ]
    cols = [c for c in required if c in df.columns]
    if len(cols) < 5:
        return pd.DataFrame()
    result = df[cols].copy()
    if "sg_uf" in result.columns:
        result["sg_regiao"] = result["sg_uf"].map(UF_REGIAO).fillna("Desconhecida")
    return result
