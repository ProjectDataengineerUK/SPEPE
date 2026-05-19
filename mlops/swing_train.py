"""
Job M3: Swing Model training (Presidente / Governador).
Usa pares 2018→2022 para estimar pct_2022 − pct_2018 = swing histórico.
Salva modelo em GCS (ou local) para inferência 2026.

Uso:
  python -m mlops.swing_train --cargo governador --uf RJ
  python -m mlops.swing_train --cargo presidente  # todas as UFs
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from mlops.swing_model import SWING_FEATURE_COLS, SwingModel

logger = logging.getLogger("spepe.mlops.swing_train")

_MODEL_VERSION = "swing-v1"

# cd_cargo TSE: 1 = Presidente, 3 = Governador
_CARGO_CD: dict[str, int] = {
    "presidente": 1,
    "governador": 3,
}

_SILVER_IBGE_COLS = [
    "log_populacao",
    "log_renda",
    "taxa_alfabetizacao",
]

_INCUMBENCY_COLS = [
    "is_incumbent_int",
    "trocou_partido_int",
]


# ── Dataset builder ───────────────────────────────────────────────────────────


def _load_from_bigquery(
    cargo: str,
    uf: str | None,
    project_id: str,
    gold_dataset: str,
    silver_dataset: str,
) -> pd.DataFrame:
    from google.cloud import bigquery  # lazy import

    client = bigquery.Client(project=project_id)
    cd_cargo = _CARGO_CD[cargo]

    uf_filter = f"AND e.sg_uf = '{uf.upper()}'" if uf and uf.lower() != "all" else ""

    # Check optional tables once
    def _table_exists(dataset: str, table: str) -> bool:
        try:
            client.get_table(f"{project_id}.{dataset}.{table}")
            return True
        except Exception:
            return False

    has_incumbencia = _table_exists(silver_dataset, "dim_incumbencia")
    has_ibge = _table_exists(gold_dataset, "fact_ibge_municipio")

    incumbencia_join = ""
    incumbencia_cols = "0 AS is_incumbent_int, 0 AS trocou_partido_int"
    if has_incumbencia:
        incumbencia_join = f"""
        LEFT JOIN `{project_id}.{silver_dataset}.dim_incumbencia` inc
          ON inc.nm_candidato = e.nm_candidato
         AND inc.cd_cargo = e.cd_cargo
         AND inc.sg_uf = e.sg_uf
         AND inc.ano_eleicao = e.ano_eleicao
        """
        incumbencia_cols = (
            "COALESCE(inc.is_incumbent, 0) AS is_incumbent_int,\n"
            "       COALESCE(inc.trocou_partido, 0) AS trocou_partido_int"
        )

    ibge_join = ""
    ibge_cols = "0.0 AS log_populacao, 0.0 AS log_renda, 50.0 AS taxa_alfabetizacao"
    if has_ibge:
        ibge_join = f"""
        LEFT JOIN `{project_id}.{gold_dataset}.fact_ibge_municipio` ibge
          ON ibge.cd_municipio_ibge = e.cd_municipio_ibge
        """
        ibge_cols = (
            "COALESCE(ibge.log_populacao, 0.0) AS log_populacao,\n"
            "       COALESCE(ibge.log_renda, 0.0) AS log_renda,\n"
            "       COALESCE(ibge.taxa_alfabetizacao, 50.0) AS taxa_alfabetizacao"
        )

    query = f"""
    WITH votos_base AS (
      SELECT
        e.nm_candidato,
        e.sg_uf,
        e.cd_municipio_ibge,
        e.sg_partido,
        e.ano_eleicao,
        e.qt_votos_validos,
        SUM(e.qt_votos_validos) OVER (
          PARTITION BY e.cd_municipio_ibge, e.ano_eleicao
        ) AS total_municipio
      FROM `{project_id}.{gold_dataset}.fact_municipio_candidato_eleicao` e
      WHERE e.cd_cargo = {cd_cargo}
        AND e.ano_eleicao IN (2018, 2022)
        {uf_filter}
    ),
    votos_pct AS (
      SELECT
        nm_candidato,
        sg_uf,
        cd_municipio_ibge,
        sg_partido,
        ano_eleicao,
        SAFE_DIVIDE(qt_votos_validos, NULLIF(total_municipio, 0)) AS pct_votos
      FROM votos_base
      WHERE total_municipio > 0
    ),
    pares AS (
      SELECT
        v2022.nm_candidato,
        v2022.sg_uf,
        v2022.cd_municipio_ibge,
        v2022.sg_partido,
        v2022.ano_eleicao,
        v2022.pct_votos,
        COALESCE(v2018.pct_votos, 0.0) AS pct_votos_historico,
        (v2022.pct_votos - COALESCE(v2018.pct_votos, 0.0)) AS swing_target
      FROM votos_pct v2022
      LEFT JOIN votos_pct v2018
        ON v2018.nm_candidato = v2022.nm_candidato
       AND v2018.cd_municipio_ibge = v2022.cd_municipio_ibge
       AND v2018.ano_eleicao = 2018
      WHERE v2022.ano_eleicao = 2022
    )
    SELECT
      p.nm_candidato,
      p.sg_uf,
      p.cd_municipio_ibge AS cd_municipio,
      p.sg_partido,
      p.ano_eleicao,
      p.pct_votos,
      p.pct_votos_historico,
      p.swing_target,
      {ibge_cols},
      {incumbencia_cols}
    FROM pares p
    {ibge_join}
    {incumbencia_join}
    WHERE p.pct_votos IS NOT NULL
    ORDER BY p.sg_uf, p.cd_municipio_ibge, p.nm_candidato
    """

    logger.info("Querying BigQuery Gold (cargo=%s, uf=%s)...", cargo, uf or "all")
    df = client.query(query).to_dataframe()
    logger.info("BigQuery returned %d rows", len(df))
    return df


def _load_from_local(
    cargo: str,
    uf: str | None,
    silver_dir: Path,
) -> pd.DataFrame:
    cd_cargo = _CARGO_CD[cargo]
    tse_files = sorted(silver_dir.glob("tse_*.parquet"))
    ibge_files = sorted(silver_dir.glob("ibge_*.parquet"))

    if not tse_files:
        logger.warning("No Silver TSE parquet files found in %s", silver_dir)
        return pd.DataFrame()

    frames = []
    for f in tse_files:
        try:
            chunk = pd.read_parquet(f)
            frames.append(chunk)
        except Exception as exc:
            logger.warning("Cannot read %s: %s", f, exc)

    if not frames:
        return pd.DataFrame()

    df_tse = pd.concat(frames, ignore_index=True)

    # Normalize column names
    df_tse.columns = [c.lower() for c in df_tse.columns]

    # Filter cargo
    cargo_col = next((c for c in ["cd_cargo", "cd_cargo_eleicao"] if c in df_tse.columns), None)
    if cargo_col:
        df_tse = df_tse[df_tse[cargo_col] == cd_cargo].copy()
    else:
        logger.warning("cd_cargo column not found; skipping cargo filter")

    # Filter UF
    uf_col = next((c for c in ["sg_uf", "uf"] if c in df_tse.columns), None)
    if uf and uf.lower() != "all" and uf_col:
        df_tse = df_tse[df_tse[uf_col].str.upper() == uf.upper()].copy()

    if df_tse.empty:
        logger.warning("No TSE rows after cargo/UF filter")
        return pd.DataFrame()

    # Resolve column names
    pct_col = next(
        (c for c in ["pct_votos", "qt_votos_nominais", "qt_votos_validos"] if c in df_tse.columns),
        None,
    )
    cand_col = next((c for c in ["nm_candidato", "nm_urna_candidato"] if c in df_tse.columns), None)
    mun_col = next(
        (
            c
            for c in ["cd_municipio_ibge", "cd_municipio", "cod_municipio_ibge"]
            if c in df_tse.columns
        ),
        None,
    )
    ano_col = next((c for c in ["ano_eleicao", "ano"] if c in df_tse.columns), None)
    partido_col = next((c for c in ["sg_partido", "partido"] if c in df_tse.columns), None)
    uf_col = uf_col or "sg_uf"

    if not all([pct_col, cand_col, mun_col, ano_col]):
        logger.warning(
            "Required columns missing in Silver TSE (pct=%s, cand=%s, mun=%s, ano=%s)",
            pct_col,
            cand_col,
            mun_col,
            ano_col,
        )
        return pd.DataFrame()

    df_tse = df_tse.rename(
        columns={
            pct_col: "pct_votos_raw",
            cand_col: "nm_candidato",
            mun_col: "cd_municipio",
            ano_col: "ano_eleicao",
            uf_col: "sg_uf",
        }
    )
    if partido_col and partido_col != "sg_partido":
        df_tse = df_tse.rename(columns={partido_col: "sg_partido"})
    elif "sg_partido" not in df_tse.columns:
        df_tse["sg_partido"] = "OUTROS"

    df_tse["ano_eleicao"] = pd.to_numeric(df_tse["ano_eleicao"], errors="coerce")
    df_tse = df_tse[df_tse["ano_eleicao"].isin([2018, 2022])].copy()

    # Compute pct_votos if raw column is vote counts
    if df_tse["pct_votos_raw"].max() > 1.5:
        totals = df_tse.groupby(["cd_municipio", "ano_eleicao"])["pct_votos_raw"].transform("sum")
        df_tse["pct_votos"] = df_tse["pct_votos_raw"] / totals.replace(0, np.nan)
    else:
        df_tse["pct_votos"] = df_tse["pct_votos_raw"]

    df_tse = df_tse.dropna(subset=["pct_votos"]).copy()

    # Build pairs: 2022 left-join 2018
    df_2022 = df_tse[df_tse["ano_eleicao"] == 2022].copy()
    df_2018 = df_tse[df_tse["ano_eleicao"] == 2018][
        ["nm_candidato", "cd_municipio", "pct_votos"]
    ].rename(columns={"pct_votos": "pct_votos_historico"})

    df_pairs = df_2022.merge(df_2018, on=["nm_candidato", "cd_municipio"], how="left")
    df_pairs["pct_votos_historico"] = df_pairs["pct_votos_historico"].fillna(0.0)
    df_pairs["swing_target"] = df_pairs["pct_votos"] - df_pairs["pct_votos_historico"]

    # IBGE join
    ibge_defaults = {c: 0.0 for c in _SILVER_IBGE_COLS}
    ibge_defaults["taxa_alfabetizacao"] = 50.0

    if ibge_files:
        ibge_frames = []
        for f in ibge_files:
            try:
                ibge_frames.append(pd.read_parquet(f))
            except Exception:
                pass
        if ibge_frames:
            df_ibge = pd.concat(ibge_frames, ignore_index=True)
            df_ibge.columns = [c.lower() for c in df_ibge.columns]
            mun_ibge_col = next(
                (
                    c
                    for c in ["cd_municipio_ibge", "cd_municipio", "cod_municipio_ibge"]
                    if c in df_ibge.columns
                ),
                None,
            )
            if mun_ibge_col:
                df_ibge = df_ibge.rename(columns={mun_ibge_col: "cd_municipio"})
                for col in _SILVER_IBGE_COLS:
                    # Accept aliases: populacao_total → log (compute on the fly)
                    if col not in df_ibge.columns:
                        if col == "log_populacao" and "populacao_total" in df_ibge.columns:
                            df_ibge["log_populacao"] = np.log1p(df_ibge["populacao_total"])
                        elif col == "log_renda" and "renda_per_capita" in df_ibge.columns:
                            df_ibge["log_renda"] = np.log1p(df_ibge["renda_per_capita"])
                keep_cols = ["cd_municipio"] + [
                    c for c in _SILVER_IBGE_COLS if c in df_ibge.columns
                ]
                df_ibge = df_ibge[keep_cols].drop_duplicates("cd_municipio")
                df_pairs = df_pairs.merge(df_ibge, on="cd_municipio", how="left")

    for col, default in ibge_defaults.items():
        if col not in df_pairs.columns:
            df_pairs[col] = default
        else:
            df_pairs[col] = df_pairs[col].fillna(default)

    for col in _INCUMBENCY_COLS:
        if col not in df_pairs.columns:
            df_pairs[col] = 0

    result = df_pairs[
        [
            "nm_candidato",
            "sg_uf",
            "cd_municipio",
            "sg_partido",
            "ano_eleicao",
            "pct_votos",
            "pct_votos_historico",
            "swing_target",
        ]
        + _SILVER_IBGE_COLS
        + _INCUMBENCY_COLS
    ].copy()

    logger.info("Local Silver returned %d rows", len(result))
    return result


def build_swing_dataset(
    cargo: str,
    uf: str | None = None,
    project_id: str | None = None,
    gold_dataset: str = "spepe_gold",
    silver_dataset: str = "spepe_silver",
) -> pd.DataFrame:
    if cargo not in _CARGO_CD:
        raise ValueError(f"cargo must be one of {list(_CARGO_CD.keys())}, got '{cargo}'")

    use_bq = os.environ.get("USE_BIGQUERY", "").lower() in ("true", "1", "yes") and bool(
        project_id or os.environ.get("GCP_PROJECT_ID")
    )

    if use_bq:
        pid = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-dev")
        try:
            return _load_from_bigquery(cargo, uf, pid, gold_dataset, silver_dataset)
        except Exception as exc:
            logger.warning("BigQuery load failed (%s) — falling back to local Silver", exc)

    silver_dir = Path(os.environ.get("SILVER_DIR", "data/silver"))
    return _load_from_local(cargo, uf, silver_dir)


# ── Model training ────────────────────────────────────────────────────────────


def train_swing_model(
    df: pd.DataFrame,
    cargo: str,
    uf: str | None,
    n_sim: int = 5000,
) -> tuple[SwingModel, dict]:
    from sklearn.model_selection import train_test_split
    from scipy.stats import pearsonr

    if df.empty:
        raise ValueError("Training dataset is empty — cannot train swing model")

    df_train, df_test = train_test_split(df, test_size=0.2, random_state=42)
    y_test = df_test["swing_target"].fillna(0.0)

    logger.info(
        "Training SwingModel: cargo=%s uf=%s n_train=%d n_test=%d features=%d",
        cargo,
        uf or "all",
        len(df_train),
        len(df_test),
        len(SWING_FEATURE_COLS),
    )

    model = SwingModel(cargo=cargo, uf=uf)
    model.fit(df_train)

    y_pred = model.predict(df_test)

    mae = float(np.mean(np.abs(y_pred - y_test.values)))
    rmse = float(np.sqrt(np.mean((y_pred - y_test.values) ** 2)))

    corr, pval = pearsonr(y_pred, y_test.values) if len(y_test) > 2 else (0.0, 1.0)

    # Monte Carlo simulation to get prediction interval width
    mc_preds = model.predict_mc(df_test, n_sim=n_sim) if hasattr(model, "predict_mc") else None
    mc_interval_width: float | None = None
    if mc_preds is not None:
        intervals = np.percentile(mc_preds, [2.5, 97.5], axis=0)
        mc_interval_width = float(np.mean(intervals[1] - intervals[0]))

    feat_importance: dict[str, float] = {}
    if hasattr(model, "feature_importances_"):
        feat_importance = {
            col: float(v) for col, v in zip(SWING_FEATURE_COLS, model.feature_importances_)
        }

    metrics = {
        "cargo": cargo,
        "uf": uf or "all",
        "n_train": len(df_train),
        "n_test": len(df_test),
        "mae": mae,
        "rmse": rmse,
        "pearson_r": float(corr),
        "pearson_pval": float(pval),
        "n_sim": n_sim,
        "mc_interval_width_95": mc_interval_width,
        "feature_importances": feat_importance,
        "model_version": _MODEL_VERSION,
    }

    logger.info(
        "Metrics — MAE=%.4f  RMSE=%.4f  Pearson r=%.4f (p=%.4f)",
        mae,
        rmse,
        corr,
        pval,
    )
    if mc_interval_width is not None:
        logger.info("MC 95%% interval width (mean): %.4f", mc_interval_width)

    return model, metrics


# ── Artifact persistence ──────────────────────────────────────────────────────


def save_artifact(
    model: SwingModel,
    metrics: dict,
    cargo: str,
    uf: str | None,
    output_dir: str | Path,
) -> str:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    label = f"swing_{cargo}_{(uf or 'all').lower()}"
    model_path = out / f"{label}.cbm"
    meta_path = out / f"{label}_metadata.json"

    model.save(str(model_path))
    logger.info("Model saved → %s", model_path)

    meta_out = {**metrics}
    # Ensure JSON-serialisable: convert numpy scalars
    for k, v in meta_out.items():
        if isinstance(v, (np.floating, np.integer)):
            meta_out[k] = float(v)
        elif isinstance(v, dict):
            meta_out[k] = {
                kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                for kk, vv in v.items()
            }

    meta_out["model_path"] = str(model_path)
    meta_out["trained_at"] = pd.Timestamp.utcnow().isoformat()

    meta_path.write_text(json.dumps(meta_out, indent=2, ensure_ascii=False))
    logger.info("Metadata saved → %s", meta_path)

    gcs_bucket = os.environ.get("GCS_BUCKET", "")
    if gcs_bucket:
        _upload_to_gcs(model_path, meta_path, cargo, uf, gcs_bucket)

    return str(out)


def _upload_to_gcs(
    model_path: Path,
    meta_path: Path,
    cargo: str,
    uf: str | None,
    bucket_name: str,
) -> None:
    try:
        from google.cloud import storage as gcs  # lazy

        ts = pd.Timestamp.utcnow().strftime("%Y%m%dT%H%M%S")
        prefix = f"models/{_MODEL_VERSION}/{cargo}/{uf or 'all'}/{ts}"
        client = gcs.Client()
        bucket = client.bucket(bucket_name)

        for local_path in (model_path, meta_path):
            blob_name = f"{prefix}/{local_path.name}"
            bucket.blob(blob_name).upload_from_filename(str(local_path))
            logger.info("Uploaded → gs://%s/%s", bucket_name, blob_name)
    except Exception as exc:
        logger.error("GCS upload failed: %s — local artifact intact", exc)


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Train SPEPE Swing Model (M3)")
    parser.add_argument(
        "--cargo",
        choices=list(_CARGO_CD.keys()),
        default="governador",
        help="Electoral office to model",
    )
    parser.add_argument(
        "--uf",
        default=None,
        help="UF filter (e.g. RJ) or 'all' for all UFs",
    )
    parser.add_argument(
        "--output-dir",
        default="data/models",
        help="Directory to write model and metadata files",
    )
    parser.add_argument(
        "--n-sim",
        type=int,
        default=5000,
        help="Monte Carlo simulations for prediction interval evaluation",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="GCP project ID (overrides GCP_PROJECT_ID env var)",
    )
    parser.add_argument(
        "--gold-dataset",
        default="spepe_gold",
        help="BigQuery Gold dataset name",
    )
    parser.add_argument(
        "--silver-dataset",
        default="spepe_silver",
        help="BigQuery Silver dataset name",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info(
        "swing_train start — cargo=%s uf=%s output=%s n_sim=%d",
        args.cargo,
        args.uf or "all",
        args.output_dir,
        args.n_sim,
    )

    df = build_swing_dataset(
        cargo=args.cargo,
        uf=args.uf,
        project_id=args.project_id,
        gold_dataset=args.gold_dataset,
        silver_dataset=args.silver_dataset,
    )

    if df.empty:
        logger.warning(
            "No data available for cargo=%s uf=%s — exiting (code 0)",
            args.cargo,
            args.uf or "all",
        )
        sys.exit(0)

    model, metrics = train_swing_model(
        df=df,
        cargo=args.cargo,
        uf=args.uf,
        n_sim=args.n_sim,
    )

    artifact_dir = save_artifact(
        model=model,
        metrics=metrics,
        cargo=args.cargo,
        uf=args.uf,
        output_dir=args.output_dir,
    )

    print("\n" + "=" * 70)
    print("Swing Model (M3) — Training Summary")
    print("=" * 70)
    print(f"Cargo:              {args.cargo}")
    print(f"UF:                 {args.uf or 'all'}")
    print(f"Train rows:         {metrics['n_train']}")
    print(f"Test rows:          {metrics['n_test']}")
    print(f"MAE:                {metrics['mae']:.4f}")
    print(f"RMSE:               {metrics['rmse']:.4f}")
    print(f"Pearson r:          {metrics['pearson_r']:.4f}  (p={metrics['pearson_pval']:.4f})")
    if metrics.get("mc_interval_width_95") is not None:
        print(f"MC 95% interval:    {metrics['mc_interval_width_95']:.4f} (mean width)")
    if metrics.get("feature_importances"):
        top = sorted(metrics["feature_importances"].items(), key=lambda x: -x[1])[:5]
        print("Top features:       " + ", ".join(f"{k}={v:.3f}" for k, v in top))
    print(f"Artifact dir:       {artifact_dir}")
    print(f"Model version:      {_MODEL_VERSION}")
    print("=" * 70)


if __name__ == "__main__":
    main()
