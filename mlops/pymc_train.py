"""PyMC hierarchical model training pipeline."""

from __future__ import annotations

import json
import logging
import os
import tempfile

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.pymc_train")

_MODEL_VERSION = "pymc-hierarchical-noncentered-v3"


def train_pymc_model(
    project_id: str | None = None,
    dataset_id: str = "spepe_mlops",
    draws: int = 1000,
    tune: int = 2000,
    chains: int = 4,
    target_accept: float = 0.95,
    save_trace: bool = True,
) -> dict:
    """Train non-centered hierarchical Bayesian model for electoral prediction.

    Model structure (non-centered parameterization):
    - Intercept: mu_a (population mean) + s_a*a_raw (UF-level deviation)
    - Slopes: mu_b[f] (feature mean) + s_b[f]*b_raw[uf,f] (UF×feature deviation)
    - Dispersion: phi ~ Gamma(2, 0.1) (learned Beta concentration)
    - Likelihood: Beta(alpha=p*phi, beta=(1-p)*phi) for proportion targets

    Train split: 2018 data (split_type='train').
    Validation split: 2022 data (split_type='validate') — out-of-sample Brier.

    Args:
        project_id: GCP project (default: env GCP_PROJECT_ID or 'spepe-prod')
        dataset_id: BigQuery dataset for model artifacts (default 'spepe_mlops')
        draws: MCMC draws per chain (default 1000)
        tune: tuning/burn-in steps (default 2000 — required for hierarchical NUTS)
        chains: parallel chains (default 4 — required for reliable R-hat)
        target_accept: NUTS target acceptance rate (default 0.95)
        save_trace: persist idata + metadata to GCS + BigQuery (default True)

    Returns:
        Dict with model, InferenceData (idata), metrics, and convergence diagnostics
    """
    try:
        import pymc as pm
        import arviz as az
    except ImportError:
        raise ImportError("PyMC dependencies missing. Install: pip install pymc arviz pytensor")

    from google.cloud import bigquery
    from mlops.eval.training_dataset_builder import build_training_dataset
    from mlops.pymc_model import build_hierarchical_model, sample_posterior

    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")

    # ── Step 1: Load full dataset (train + val) ─────────────────────────────
    logger.info("Loading training dataset (split_mode='all')...")
    df = build_training_dataset(project_id=project_id, write_to_bq=False, split_mode="all")

    if df.empty:
        raise ValueError("Training dataset is empty")

    # ── Step 2: Encode UF categories on full dataset (consistent train+val) ─
    uf_cat = pd.Categorical(df["sg_uf"])
    uf_categories = list(uf_cat.categories)
    df["uf_idx"] = uf_cat.codes
    df["y_continuous"] = df["pct_votos"].clip(0.001, 0.999)

    # ── Step 3: Train / validation split ────────────────────────────────────
    df_train = df[df["split_type"] == "train"].copy().reset_index(drop=True)
    df_val = df[df["split_type"] == "validate"].copy().reset_index(drop=True)

    if df_train.empty:
        raise ValueError("Train partition (split_type='train') is empty — check Silver data")
    if df_val.empty:
        logger.warning("Validation partition is empty — no out-of-sample Brier available")

    logger.info(
        "Dataset loaded: train=%d rows, val=%d rows, UFs=%d",
        len(df_train),
        len(df_val),
        len(uf_categories),
    )

    # ── Step 4: Feature selection — only columns with real signal ────────────
    feature_cols = [
        "populacao",
        "renda_media",
        "pct_ensino_superior",
        "pct_analfabetos",
    ]
    # Hard QC: abort if any feature is degenerate (all-constant after z-score)
    train_raw = df_train[feature_cols].fillna(0)
    degenerate = train_raw.columns[train_raw.std() < 1e-3].tolist()
    if degenerate:
        raise ValueError(
            f"Degenerate features (std < 1e-3) detected: {degenerate}. "
            "Fix data pipeline before training."
        )

    # ── Step 5: Stratified sub-sampling — train only ─────────────────────────
    max_rows = int(os.environ.get("PYMC_MAX_ROWS", "10000"))
    if len(df_train) > max_rows:
        n_uf_train = df_train["sg_uf"].nunique()
        rows_per_uf = max(10, max_rows // n_uf_train)
        df_train = (
            df_train.groupby("sg_uf", group_keys=False)
            .apply(lambda g: g.sample(min(len(g), rows_per_uf), random_state=42))
            .reset_index(drop=True)
        )
        if len(df_train) > max_rows:
            df_train = df_train.sample(max_rows, random_state=42).reset_index(drop=True)
        logger.info(
            "Sub-sampled train to %d rows (stratified by %d UFs)", len(df_train), n_uf_train
        )

    # ── Step 6: Normalize features using train statistics ────────────────────
    train_mean = (
        train_raw.loc[df_train.index, feature_cols].mean()
        if len(df_train) < len(train_raw)
        else train_raw.mean()
    )
    train_std = (
        train_raw.loc[df_train.index, feature_cols].std()
        if len(df_train) < len(train_raw)
        else train_raw.std()
    )

    # Recompute on sub-sampled df_train
    train_feat = df_train[feature_cols].fillna(0)
    train_mean = train_feat.mean()
    train_std = train_feat.std()

    X_train = ((train_feat - train_mean) / (train_std + 1e-6)).values
    y_train = df_train["y_continuous"].values
    uf_train = df_train["uf_idx"].values
    n_uf = len(uf_categories)
    n_features = X_train.shape[1]

    logger.info("Features: %s | shape: %s", feature_cols, X_train.shape)
    logger.info(
        "Target (train): min=%.4f max=%.4f mean=%.4f", y_train.min(), y_train.max(), y_train.mean()
    )

    # ── Step 7: Build and sample PyMC hierarchical model ────────────────────
    logger.info("Building hierarchical Bayesian model (non-centered)...")
    model = build_hierarchical_model(
        X=X_train,
        y=y_train,
        uf_idx=uf_train,
        n_uf=n_uf,
        n_features=n_features,
    )
    logger.info("Model compiled. Sampling %d draws × %d chains (tune=%d)...", draws, chains, tune)

    idata = sample_posterior(
        model,
        draws=draws,
        tune=tune,
        chains=chains,
        target_accept=target_accept,
        init="jitter+adapt_diag",
        random_seed=42,
    )
    logger.info("Sampling complete")

    # ── Step 8: Convergence diagnostics ─────────────────────────────────────
    summary = az.summary(idata, var_names=["mu_a", "mu_b", "s_a", "s_b", "phi"])
    rhat_max = float(summary["r_hat"].max())
    ess_bulk_min = float(summary["ess_bulk"].min())

    n_divergent = int(idata.sample_stats["diverging"].sum().values)
    n_total = idata.posterior.sizes["draw"] * idata.posterior.sizes["chain"]
    pct_divergent = 100.0 * (n_divergent / n_total)

    # ESS gate: achievable threshold (40% of total post-warmup draws)
    ess_threshold = max(400, int(0.4 * draws * chains))
    checks = {
        "rhat_converged": rhat_max < 1.01,
        "ess_sufficient": ess_bulk_min > ess_threshold,
        "no_divergences": (n_divergent / n_total) < 0.001,
    }
    all_checks_passed = all(checks.values())
    logger.info("Quality checks: %s", checks)
    logger.info(
        "Rhat max=%.4f  ESS bulk min=%.0f (threshold=%d)  Divergences=%.2f%%",
        rhat_max,
        ess_bulk_min,
        ess_threshold,
        pct_divergent,
    )
    if not all_checks_passed:
        logger.warning("Some quality checks failed — consider more draws/tune.")

    # ── Step 9: In-sample posterior predictive (train Brier) ────────────────
    logger.info("Computing in-sample Brier score...")
    with model:
        ppc_train = pm.sample_posterior_predictive(idata, random_seed=42)
    y_pred_train = ppc_train.posterior_predictive["y_obs"].mean(dim=["chain", "draw"]).values
    brier_train = float(np.mean((y_pred_train - y_train) ** 2))
    logger.info("Brier score (train, in-sample): %.4f", brier_train)

    # ── Step 10: Out-of-sample Brier on validation set ─────────────────────
    brier_oos: float | None = None
    if not df_val.empty:
        logger.info(
            "Computing out-of-sample Brier score on validation set (%d rows)...", len(df_val)
        )
        val_feat = df_val[feature_cols].fillna(0)
        X_val = ((val_feat - train_mean) / (train_std + 1e-6)).values
        y_val = df_val["y_continuous"].values
        uf_val = df_val["uf_idx"].values

        with model:
            pm.set_data({"X_data": X_val, "uf_idx_data": uf_val})
            ppc_val = pm.sample_posterior_predictive(idata, random_seed=42)
        y_pred_val = ppc_val.posterior_predictive["y_obs"].mean(dim=["chain", "draw"]).values
        brier_oos = float(np.mean((y_pred_val - y_val) ** 2))
        logger.info("Brier score (val 2022, out-of-sample): %.4f", brier_oos)

        # Restore model to train data after val evaluation
        with model:
            pm.set_data({"X_data": X_train, "uf_idx_data": uf_train})

    # ── Step 11: Persist idata + normalization stats to GCS ─────────────────
    artifact_path: str | None = None
    gcs_bucket = os.environ.get("GCS_BUCKET", "")
    ts = pd.Timestamp.utcnow().strftime("%Y%m%dT%H%M%S")
    model_prefix = f"models/{_MODEL_VERSION}/{ts}"

    if save_trace and gcs_bucket:
        try:
            from google.cloud import storage as gcs

            gcs_client = gcs.Client(project=project_id)
            bucket = gcs_client.bucket(gcs_bucket)

            # Save idata (posterior + diagnostics) as netCDF4
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
                idata_path = f.name
            idata.to_netcdf(idata_path)
            blob_idata = f"{model_prefix}/idata.nc"
            bucket.blob(blob_idata).upload_from_filename(idata_path)
            artifact_path = f"gs://{gcs_bucket}/{blob_idata}"
            logger.info("idata saved → %s", artifact_path)

            # Save normalization stats + UF mapping (required for inference)
            norm_stats = {
                "model_version": _MODEL_VERSION,
                "feature_cols": feature_cols,
                "train_mean": train_mean.tolist(),
                "train_std": train_std.tolist(),
                "uf_categories": uf_categories,
                "n_uf": n_uf,
                "n_features": n_features,
                "trained_at": ts,
            }
            bucket.blob(f"{model_prefix}/norm_stats.json").upload_from_string(
                json.dumps(norm_stats, indent=2), content_type="application/json"
            )
            logger.info(
                "Normalization stats saved → gs://%s/%s/norm_stats.json", gcs_bucket, model_prefix
            )

        except Exception as exc:
            logger.error("Failed to save idata to GCS: %s — metadata still saved to BQ", exc)

    # ── Step 12: Save run metadata to BigQuery ───────────────────────────────
    if save_trace:
        logger.info("Saving model metadata to BigQuery...")
        try:
            client = bigquery.Client(project=project_id)
            metadata_df = pd.DataFrame(
                {
                    "model_version": _MODEL_VERSION,
                    "timestamp": pd.Timestamp.utcnow().isoformat(),
                    "n_draws": draws,
                    "n_tune": tune,
                    "n_chains": chains,
                    "target_accept": target_accept,
                    "rhat_max": rhat_max,
                    "ess_bulk_min": ess_bulk_min,
                    "ess_threshold": ess_threshold,
                    "pct_divergences": pct_divergent,
                    "brier_train": brier_train,
                    "brier_oos": brier_oos if brier_oos is not None else float("nan"),
                    "all_checks_passed": bool(all_checks_passed),
                    "feature_count": n_features,
                    "effective_feature_count": n_features,
                    "uf_count": n_uf,
                    "train_sample_count": len(y_train),
                    "val_sample_count": len(df_val),
                    "artifact_path": artifact_path or "",
                },
                index=[0],
            )
            table_id = f"{project_id}.{dataset_id}.model_metadata"
            client.load_table_from_dataframe(
                metadata_df,
                table_id,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
            ).result()
            logger.info("Model metadata saved → %s", table_id)
        except Exception as exc:
            logger.error("Failed to save metadata to BQ: %s", exc)

    return {
        "model": model,
        "idata": idata,
        "summary": summary,
        "brier_train": brier_train,
        "brier_oos": brier_oos,
        "rhat_max": rhat_max,
        "ess_bulk_min": ess_bulk_min,
        "ess_threshold": ess_threshold,
        "pct_divergences": pct_divergent,
        "checks_passed": all_checks_passed,
        "n_train": len(y_train),
        "n_val": len(df_val),
        "n_features": n_features,
        "artifact_path": artifact_path,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = train_pymc_model(
        draws=int(os.environ.get("PYMC_DRAWS", "1000")),
        tune=int(os.environ.get("PYMC_TUNE", "2000")),
        chains=int(os.environ.get("PYMC_CHAINS", "4")),
        target_accept=float(os.environ.get("PYMC_TARGET_ACCEPT", "0.95")),
        save_trace=True,
    )

    print("\n" + "=" * 70)
    print("PyMC Hierarchical Bayesian Model — Training Summary")
    print("=" * 70)
    brier_oos = result.get("brier_oos")
    print(f"Brier Score (train, in-sample):     {result['brier_train']:.4f}")
    print(
        f"Brier Score (val 2022, OOS):         {brier_oos:.4f}"
        if brier_oos is not None
        else "Brier Score (val OOS):               N/A"
    )
    print(f"Rhat Max:                            {result['rhat_max']:.4f} (target < 1.01)")
    print(
        f"ESS Bulk Min:                        {result['ess_bulk_min']:.0f} (threshold {result['ess_threshold']})"
    )
    print(f"Divergences:                         {result['pct_divergences']:.2f}% (target < 0.1%)")
    print(f"Checks Passed:                       {result['checks_passed']}")
    print(f"Model Version:                       {_MODEL_VERSION}")
    print(f"Train samples:                       {result['n_train']}")
    print(f"Val samples:                         {result['n_val']}")
    print(f"Features:                            {result['n_features']}")
    if result.get("artifact_path"):
        print(f"Artifact:                            {result['artifact_path']}")
    print("=" * 70)
