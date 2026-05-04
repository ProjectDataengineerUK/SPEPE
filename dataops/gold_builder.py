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


def _build_gold_via_bigquery_sql() -> dict:
    """Build all Gold tables using BigQuery SQL — no data movement to Python."""
    from google.cloud import bigquery

    client = bigquery.Client(project=_GCP_PROJECT)
    _BQ_GOLD_DATASET = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
    silver_wc = f"`{_GCP_PROJECT}.{_BQ_SILVER_DATASET}.tse_*`"
    gold = f"{_GCP_PROJECT}.{_BQ_GOLD_DATASET}"

    silver = f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}"
    sqls = {
        "fact_municipio_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_municipio_eleicao` AS
            SELECT
                sg_uf, cd_municipio, nm_municipio, cd_cargo, ds_cargo,
                ano_eleicao,
                SUM(qt_votos) AS total_votos,
                COUNT(DISTINCT nr_candidato) AS n_candidatos,
                COUNT(DISTINCT CONCAT(CAST(nr_zona AS STRING), '-', CAST(nr_secao AS STRING))) AS n_secoes,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, cd_municipio, nm_municipio, cd_cargo, ds_cargo, ano_eleicao
        """,
        "fact_secao_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_secao_eleicao` AS
            SELECT
                sg_uf, cd_municipio, nm_municipio, nr_zona, nr_secao,
                nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao,
                SUM(qt_votos) AS total_votos,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, cd_municipio, nm_municipio, nr_zona, nr_secao,
                     nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao
        """,
        "fact_candidato_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_candidato_eleicao` AS
            SELECT
                sg_uf, nr_candidato, nm_candidato, sg_partido, cd_cargo, ds_cargo,
                ano_eleicao,
                SUM(qt_votos) AS total_votos,
                COUNT(DISTINCT cd_municipio) AS n_municipios,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, nr_candidato, nm_candidato, sg_partido, cd_cargo, ds_cargo, ano_eleicao
        """,
        "fact_municipio_candidato_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_municipio_candidato_eleicao` AS
            SELECT
                sg_uf, cd_municipio, nm_municipio, cd_municipio_ibge,
                nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao,
                SUM(qt_votos) AS total_votos,
                ROUND(
                    SUM(qt_votos) / NULLIF(SUM(SUM(qt_votos)) OVER (
                        PARTITION BY sg_uf, cd_municipio, cd_cargo, nr_turno, ano_eleicao
                    ), 0) * 100, 1
                ) AS pct_votos_municipio,
                ROW_NUMBER() OVER (
                    PARTITION BY sg_uf, cd_municipio, cd_cargo, nr_turno, ano_eleicao
                    ORDER BY SUM(qt_votos) DESC
                ) AS rn_municipio,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, cd_municipio, nm_municipio, cd_municipio_ibge,
                     nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao
        """,
        "fact_ibge_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_ibge_municipio` AS
            SELECT
                SAFE_CAST(cd_municipio_ibge AS INT64)          AS cd_municipio_ibge,
                sg_uf,
                ANY_VALUE(nm_municipio)                         AS nm_municipio,
                MAX(SAFE_CAST(ano AS INT64))                    AS ano,
                MAX(SAFE_CAST(idhm AS FLOAT64))                 AS idhm,
                MAX(SAFE_CAST(renda_per_capita AS FLOAT64))     AS renda_per_capita,
                MAX(SAFE_CAST(gini AS FLOAT64))                 AS gini,
                MAX(SAFE_CAST(pct_extrema_pobreza AS FLOAT64))  AS pct_extrema_pobreza,
                MAX(SAFE_CAST(taxa_analfabetismo AS FLOAT64))   AS taxa_analfabetismo,
                MAX(SAFE_CAST(pct_urbano AS FLOAT64))           AS pct_urbano,
                MAX(SAFE_CAST(populacao_total AS FLOAT64))      AS populacao_total,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.ibge_*`
            WHERE cd_municipio_ibge IS NOT NULL
            GROUP BY cd_municipio_ibge, sg_uf
        """,
        "fact_seguranca_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_seguranca_municipio` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)                    AS cd_municipio_ibge,
                sg_uf,
                COALESCE(SAFE_CAST(ano AS INT64), 2022)             AS ano,
                COALESCE(
                    SAFE_CAST(ivs_total AS FLOAT64),
                    SAFE_CAST(ivs_valor AS FLOAT64)
                )                                                   AS ivs_total,
                SAFE_CAST(ivs_infraestrutura AS FLOAT64)            AS ivs_infraestrutura,
                SAFE_CAST(ivs_capital_humano AS FLOAT64)            AS ivs_capital_humano,
                SAFE_CAST(ivs_renda_trabalho AS FLOAT64)            AS ivs_renda_trabalho,
                COALESCE(
                    SAFE_CAST(taxa_homicidio_100k AS FLOAT64),
                    SAFE_CAST(taxa_homicidio AS FLOAT64)
                )                                                   AS taxa_homicidio_100k,
                SAFE_CAST(taxa_roubo_100k AS FLOAT64)               AS taxa_roubo_100k,
                SAFE_CAST(qt_feminicidio AS INT64)                  AS qt_feminicidio,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.seguranca_municipal`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
        "fact_saude_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_saude_municipio` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)             AS cd_municipio_ibge,
                sg_uf,
                COALESCE(SAFE_CAST(ano AS INT64), 2022)      AS ano,
                COALESCE(
                    SAFE_CAST(taxa_mortalidade_infantil_1000 AS FLOAT64),
                    SAFE_CAST(tx_mortalidade_infantil AS FLOAT64)
                )                                            AS taxa_mortalidade_infantil_1000,
                SAFE_CAST(taxa_mortalidade_materna_100k AS FLOAT64) AS taxa_mortalidade_materna_100k,
                COALESCE(
                    SAFE_CAST(pct_cobertura_plano_saude AS FLOAT64),
                    SAFE_CAST(cobertura_esf_pct AS FLOAT64)
                )                                            AS pct_cobertura_plano_saude,
                SAFE_CAST(idsus_score AS FLOAT64)            AS idsus_score,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.saude_municipal`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
        "fact_social_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_social_municipio` AS
            SELECT
                sg_uf,
                fonte,
                DATE(created_at) AS data_referencia,
                COUNT(*) AS qt_posts,
                COALESCE(SUM(like_count), 0)    AS total_likes,
                COALESCE(SUM(retweet_count), 0) AS total_retweets,
                COALESCE(SUM(reply_count), 0)   AS total_comments,
                COALESCE(SUM(like_count), 0) + COALESCE(SUM(retweet_count), 0)
                    + COALESCE(SUM(reply_count), 0) AS total_engajamento,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.social_mencoes_br`
            GROUP BY sg_uf, fonte, DATE(created_at)
        """,
        # fact_pesquisa: promote Silver fact_pesquisa (multi-ano) → Gold
        # Skipped silently if Silver table does not exist yet
        "fact_pesquisa": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_pesquisa` AS
            SELECT * FROM `{silver}.fact_pesquisa`
        """,
    }

    # Tables that may have no Silver source yet — skip without failing the job
    _OPTIONAL = {"fact_pesquisa", "fact_social_municipio", "fact_economico_municipio"}

    results = {}
    for table_name, sql in sqls.items():
        try:
            client.query(f"DROP TABLE IF EXISTS `{gold}.{table_name}`").result()
            job = client.query(sql)
            job.result()
            row_count = (
                client.query(f"SELECT COUNT(*) FROM `{gold}.{table_name}`")
                .to_dataframe(create_bqstorage_client=False)
                .iloc[0, 0]
            )
            results[table_name] = {"path": f"{gold}.{table_name}", "rows": int(row_count)}
            logger.info("Gold BQ SQL: %s (%d rows)", table_name, row_count)
        except Exception as exc:
            if table_name in _OPTIONAL:
                logger.warning("Gold BQ SQL skipped %s (Silver ainda vazio): %s", table_name, exc)
                results[table_name] = {"status": "skipped", "message": str(exc)}
            else:
                logger.error("Gold BQ SQL falhou para %s: %s", table_name, exc)
                results[table_name] = {"status": "error", "message": str(exc)}

    return {"status": "ok", "tables": results}


def _load_silver_from_bigquery() -> pd.DataFrame:
    """Read all tse_* tables from BigQuery Silver dataset (local/small UFs only)."""
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
    """Build all Gold tables from Silver layer."""
    LOCAL_GOLD_DIR.mkdir(parents=True, exist_ok=True)

    # BQ path: run aggregations as SQL — no data movement to Python
    if use_bigquery and _GCP_PROJECT:
        return _build_gold_via_bigquery_sql()

    df_all = pd.DataFrame()
    if df_all.empty:
        silver_files = list(LOCAL_SILVER_DIR.glob("tse_*.parquet"))
        if not silver_files:
            return {
                "status": "error",
                "message": "Nenhum arquivo Silver disponível. Execute silver_transform primeiro.",
            }
        df_all = pd.concat([pd.read_parquet(f) for f in silver_files], ignore_index=True)

    result = {}

    # ── Eleitoral (TSE) ───────────────────────────────────────────────────────
    fact_mun = _build_fact_municipio_eleicao(df_all)
    result["fact_municipio_eleicao"] = _write_gold(fact_mun, "fact_municipio_eleicao", use_bigquery)

    fact_sec = _build_fact_secao_eleicao(df_all)
    result["fact_secao_eleicao"] = _write_gold(fact_sec, "fact_secao_eleicao", use_bigquery)

    fact_cand = _build_fact_candidato_dia(df_all)
    result["fact_candidato_dia"] = _write_gold(fact_cand, "fact_candidato_dia", use_bigquery)

    # ── Pesquisas ─────────────────────────────────────────────────────────────
    fact_pesq = _build_fact_pesquisa()
    result["fact_pesquisa"] = _write_gold(fact_pesq, "fact_pesquisa", use_bigquery)

    # ── IBGE indicadores municipais ───────────────────────────────────────────
    ibge_data = _load_ibge_silver()
    fact_ibge = _build_fact_ibge_municipio(ibge_data)
    result["fact_ibge_municipio"] = _write_gold(fact_ibge, "fact_ibge_municipio", use_bigquery)

    # ── Segurança pública ─────────────────────────────────────────────────────
    fact_seg = _build_fact_seguranca()
    result["fact_seguranca_municipio"] = _write_gold(
        fact_seg, "fact_seguranca_municipio", use_bigquery
    )

    # ── Saúde / DataSUS ───────────────────────────────────────────────────────
    fact_saude = _build_fact_saude()
    result["fact_saude_municipio"] = _write_gold(fact_saude, "fact_saude_municipio", use_bigquery)

    # ── Social ────────────────────────────────────────────────────────────────
    fact_social = _build_fact_social()
    result["fact_social_municipio"] = _write_gold(
        fact_social, "fact_social_municipio", use_bigquery
    )

    # ── Economia (DIEESE + CETIC) ─────────────────────────────────────────────
    fact_eco = _build_fact_economico()
    result["fact_economico_municipio"] = _write_gold(
        fact_eco, "fact_economico_municipio", use_bigquery
    )

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

    group_cols = ["nm_candidato"]
    if "ano_eleicao" in df.columns:
        group_cols.append("ano_eleicao")
    if "sg_uf" in df.columns:
        group_cols.append("sg_uf")

    agg_kwargs = {
        "qt_votos_total": ("qt_votos", "sum")
        if "qt_votos" in df.columns
        else ("nm_candidato", "count"),
    }
    cand_agg = df.groupby(group_cols).agg(**agg_kwargs).reset_index()

    # Partition field required by the Terraform table schema
    cand_agg["data"] = pd.Timestamp.utcnow().date()

    logger.info(f"fact_candidato_dia: {len(cand_agg)} candidatos")
    return cand_agg


def _load_ibge_silver() -> pd.DataFrame:
    ibge_files = list(LOCAL_SILVER_DIR.glob("ibge_*.parquet"))
    if not ibge_files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in ibge_files], ignore_index=True)


_FACT_PESQUISA_EMPTY_COLS = [
    "uf", "candidato", "instituto", "cd_cargo",
    "data_pesquisa_inicio", "data_pesquisa_fim",
    "intencao_pct", "house_effect", "intencao_ajustada",
    "margem_erro", "record_confidence_score", "poll_id",
    "ano", "tipo_pesquisa", "ingested_at",
]


def _build_fact_pesquisa() -> pd.DataFrame:
    """Promote Silver fact_pesquisa_*.parquet files to Gold."""
    silver_files = sorted(LOCAL_SILVER_DIR.glob("fact_pesquisa_*.parquet"))
    if not silver_files:
        logger.info("fact_pesquisa: nenhum Silver disponível — retornando vazio")
        return pd.DataFrame(columns=_FACT_PESQUISA_EMPTY_COLS)

    dfs = []
    for f in silver_files:
        try:
            dfs.append(pd.read_parquet(f))
            logger.info("fact_pesquisa Silver: %s (%d rows)", f.name, len(dfs[-1]))
        except Exception as exc:
            logger.warning("fact_pesquisa: falha ao ler %s: %s", f, exc)

    if not dfs:
        return pd.DataFrame(columns=_FACT_PESQUISA_EMPTY_COLS)

    df = pd.concat(dfs, ignore_index=True)
    logger.info("fact_pesquisa Gold: %d rows de %d arquivos Silver", len(df), len(silver_files))
    return df


def _build_fact_ibge_municipio(df_ibge: pd.DataFrame) -> pd.DataFrame:
    """Promote IBGE Silver indicators to Gold fact_ibge_municipio."""
    if df_ibge.empty:
        return pd.DataFrame()

    ibge_cols = [
        "cd_municipio_ibge",
        "nm_municipio",
        "sg_uf",
        "ano",
        "idhm",
        "idhm_educacao",
        "idhm_longevidade",
        "idhm_renda",
        "renda_per_capita",
        "gini",
        "pct_extrema_pobreza",
        "taxa_analfabetismo",
        "anos_estudo_medio",
        "pct_domicilios_agua",
        "pct_domicilios_esgoto",
        "pct_domicilios_energia",
        "populacao_total",
        "densidade_demografica",
        "pct_urbano",
    ]
    cols = [c for c in ibge_cols if c in df_ibge.columns]
    if "cd_municipio_ibge" not in cols:
        logger.warning("fact_ibge_municipio: cd_municipio_ibge ausente no Silver IBGE")
        return pd.DataFrame()

    df = df_ibge[cols].copy()
    if "sg_uf" in df.columns:
        df["sg_regiao"] = df["sg_uf"].map(UF_REGIAO).fillna("Desconhecida")
    if "ano" not in df.columns:
        df["ano"] = 0
    df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
        "Int64"
    )
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_ibge_municipio: %d rows", len(df))
    return df


def _build_fact_seguranca() -> pd.DataFrame:
    """Aggregate Silver seguranca_municipal files into Gold fact_seguranca_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("seguranca_municipal_*.parquet"))
    if not files:
        logger.info("fact_seguranca_municipio: nenhum Silver de segurança disponível")
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_seguranca_municipio: %d rows de %d arquivos Silver", len(df), len(files))
    return df


def _build_fact_saude() -> pd.DataFrame:
    """Aggregate Silver saude_municipal files into Gold fact_saude_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("saude_municipal_*.parquet"))
    if not files:
        logger.info("fact_saude_municipio: nenhum Silver de saúde disponível")
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_saude_municipio: %d rows de %d arquivos Silver", len(df), len(files))
    return df


def _build_fact_social() -> pd.DataFrame:
    """Aggregate Silver social_mencoes_br files into Gold fact_social_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("social_mencoes_br_*.parquet"))
    if not files:
        logger.info("fact_social_municipio: nenhum Silver social disponível")
        return pd.DataFrame()

    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)

    group_cols = ["sg_uf", "fonte"]
    if "created_at" in df.columns:
        df["data_referencia"] = pd.to_datetime(df["created_at"], errors="coerce").dt.date
        group_cols.append("data_referencia")
    else:
        df["data_referencia"] = pd.Timestamp.utcnow().date()
        group_cols.append("data_referencia")

    if "ano" in df.columns:
        group_cols.append("ano")

    agg: dict[str, object] = {"qt_posts": ("fonte", "count")}
    for col, alias in [
        ("like_count", "total_likes"),
        ("likes", "total_likes"),
        ("retweet_count", "total_retweets"),
        ("reply_count", "total_comments"),
        ("comments", "total_comments"),
        ("shares", "total_shares"),
    ]:
        if col in df.columns and alias not in agg:
            agg[alias] = (col, "sum")

    avail_group = [c for c in group_cols if c in df.columns or c == "data_referencia"]
    fact = df.groupby([c for c in avail_group if c in df.columns], as_index=False).agg(**agg)
    fact["total_engajamento"] = sum(
        fact.get(c, 0)
        for c in ("total_likes", "total_retweets", "total_comments", "total_shares")
        if c in fact.columns
    )
    fact["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_social_municipio: %d rows", len(fact))
    return fact


def _build_fact_economico() -> pd.DataFrame:
    """Aggregate Silver economia_municipal files into Gold fact_economico_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("economia_municipal_*.parquet"))
    if not files:
        logger.info("fact_economico_municipio: nenhum Silver de economia disponível")
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_economico_municipio: %d rows de %d arquivos Silver", len(df), len(files))
    return df


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
                # Prefer int64 to match BQ INTEGER; fall back to float64 only if NaN present
                if df[col].isna().any():
                    df[col] = df[col].astype("float64")
                else:
                    df[col] = df[col].astype("int64")
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

        # Determine partitioning strategy: integer fields need RangePartitioning
        time_partitioning = None
        range_partitioning = None
        if partition_field and partition_field in df.columns:
            dtype_str = str(df[partition_field].dtype)
            if dtype_str in ("int64", "float64"):
                df[partition_field] = df[partition_field].fillna(0).astype("int64")
                range_partitioning = bigquery.RangePartitioning(
                    field=partition_field,
                    range_=bigquery.PartitionRange(start=2010, end=2031, interval=1),
                )
            else:
                time_partitioning = bigquery.TimePartitioning(field=partition_field)

        # When table already exists (e.g. Terraform-managed), don't supply schema —
        # BQ validates against the existing definition and rejects mode mismatches.
        try:
            client.get_table(table_id)
            table_exists = True
        except Exception:
            table_exists = False

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            create_disposition="CREATE_IF_NEEDED",
            time_partitioning=time_partitioning if not table_exists else None,
            range_partitioning=range_partitioning if not table_exists else None,
            clustering_fields=(
                ([f for f in cluster_fields if f in df.columns] if cluster_fields else None)
                if not table_exists
                else None
            ),
            autodetect=not table_exists,
            schema=_dataframe_to_bq_schema(df) if not table_exists else None,
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
