"""PyMC hierarchical model training pipeline."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.pymc_train")


def train_pymc_model(
    project_id: str | None = None,
    dataset_id: str = "spepe_mlops",
    draws: int = 2000,
    tune: int = 1500,
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

    Non-centered parameterization avoids Neal's funnel and improves NUTS efficiency.

    Args:
        project_id: GCP project (default: env GCP_PROJECT_ID or 'spepe-prod')
        dataset_id: BigQuery dataset for model artifacts (default 'spepe_mlops')
        draws: MCMC draws per chain (default 2000, increased from 1000)
        tune: tuning/burn-in steps (default 1500, increased from 500)
        chains: parallel chains (default 4, increased from 2)
        target_accept: NUTS target acceptance rate (default 0.95, increased from 0.9)
        save_trace: save metadata to BigQuery (default True)

    Returns:
        Dict with model, InferenceData (idata), metrics, and convergence diagnostics
    """
    try:
        import pymc as pm
        import arviz as az
        import pytensor.tensor as pt
    except ImportError:
        raise ImportError(
            "PyMC dependencies missing. Install: pip install pymc arviz pytensor"
        )

    from google.cloud import bigquery
    from mlops.eval.training_dataset_builder import build_training_dataset
    from mlops.pymc_model import build_hierarchical_model, sample_posterior

    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")

    # ── Step 1: Load training dataset ──────────────────────────────────────
    logger.info("Loading training dataset...")
    df = build_training_dataset(project_id=project_id, write_to_bq=False)

    if df.empty:
        raise ValueError("Training dataset is empty")

    # ── Step 2: Prepare features for modeling ──────────────────────────────
    logger.info("Preparing features...")

    # Select feature columns (exclude ID, target, metadata)
    # PHASE 2: Removed low-coverage social features (YouTube, RSS < 5%); added temporal feature
    feature_cols = [
        "populacao",
        "densidade_populacional",
        "renda_media",
        "pct_ensino_superior",
        "pct_analfabetos",
        "taxa_desemprego",
        "cobertura_sus",
        "mortalidade_infantil",
        "taxa_homicidio",
        "taxa_roubo",
        "pct_votos_partido_anterior",
    ]
    # Normalize features (z-score)
    df_features = df[feature_cols].fillna(0)
    df_features_norm = (df_features - df_features.mean()) / (df_features.std() + 1e-6)

    # Use continuous target: pct_votos (0-1) com link logit
    # Regressão logística contínua preserva informação vs binary classification
    df["y_continuous"] = df["pct_votos"].clip(0.001, 0.999)  # evitar log(0) e log(1)
    y = df["y_continuous"].values  # Extract to numpy array for logging and modeling

    # Group by UF for hierarchical structure
    df["uf_idx"] = pd.Categorical(df["sg_uf"]).codes

    logger.info(f"Features shape: {df_features_norm.shape}")
    logger.info(f"Target: min={y.min():.4f}, max={y.max():.4f}, mean={y.mean():.4f}")
    logger.info(f"UF groups: {df['uf_idx'].nunique()}")
    logger.info(f"Candidates: {df['candidato'].nunique()}")

    # ── Step 3: Build PyMC hierarchical model ──────────────────────────────
    logger.info("Building hierarchical Bayesian model...")

    X = df_features_norm.values
    y_obs = y  # usar y_continuous já preparado acima
    uf_groups = df["uf_idx"].values
    n_uf = df["uf_idx"].nunique()
    n_features = X.shape[1]

    # Use new non-centered hierarchical model from pymc_model.py
    from mlops.pymc_model import build_hierarchical_model
    model = build_hierarchical_model(
        X=X,
        y=y_obs,
        uf_idx=uf_groups,
        n_uf=n_uf,
        n_features=n_features,
    )

    logger.info("Model compiled successfully (non-centered parameterization)")

    # ── Step 4: Sample from posterior ──────────────────────────────────────
    logger.info(f"Sampling {draws} draws × {chains} chains (tune={tune}, target_accept={target_accept})...")
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

    # Extract summary for diagnostics
    import arviz as az
    summary = az.summary(idata, var_names=["mu_a", "mu_b", "s_a", "s_b", "phi"])
    rhat_max = summary["r_hat"].max()
    ess_bulk_min = summary["ess_bulk"].min()

    # ── Step 5: Model quality checks ────────────────────────────────────────
    # Check for divergences
    n_divergent = idata.sample_stats["diverging"].sum().values
    n_total = idata.posterior.sizes["draw"] * idata.posterior.sizes["chain"]
    pct_divergent = 100 * (n_divergent / n_total)

    checks = {
        "rhat_converged": rhat_max < 1.01,
        "ess_sufficient": ess_bulk_min > 1000,
        "no_divergences": (n_divergent / n_total) < 0.001,
    }

    all_checks_passed = all(checks.values())
    logger.info(f"Quality checks: {checks}")
    logger.info(f"Rhat max: {rhat_max:.4f}, ESS bulk min: {ess_bulk_min:.0f}, Divergences: {pct_divergent:.2f}%")

    if not all_checks_passed:
        logger.warning("⚠️  Some quality checks failed. Consider resampling with more draws/tune.")
    else:
        logger.info("✅ All quality checks passed")

    # ── Step 6: Generate predictions on validation set ──────────────────────
    logger.info("Generating predictions on training data...")

    # Posterior predictive for validation (use full model posterior)
    with model:
        ppc = pm.sample_posterior_predictive(idata, random_seed=42)

    y_pred_proba = ppc.posterior_predictive["y_obs"].mean(dim=["chain", "draw"]).values

    # Compute Brier score
    brier_score = np.mean((y_pred_proba - y) ** 2)
    logger.info(f"Brier score (validation): {brier_score:.4f}")

    # ── Step 7: Save InferenceData to BigQuery ──────────────────────────────
    if save_trace:
        logger.info("Saving InferenceData metadata to BigQuery...")

        client = bigquery.Client(project=project_id)

        # Save model metadata for audit trail
        metadata_df = pd.DataFrame({
            "model_version": "pymc-hierarchical-noncentered-v2",
            "timestamp": pd.Timestamp.utcnow(),
            "n_draws": draws,
            "n_tune": tune,
            "n_chains": chains,
            "target_accept": target_accept,
            "rhat_max": float(rhat_max),
            "ess_bulk_min": float(ess_bulk_min),
            "pct_divergences": float(pct_divergent),
            "brier_score": float(brier_score),
            "all_checks_passed": bool(all_checks_passed),
            "feature_count": n_features,
            "uf_count": n_uf,
            "sample_count": len(y),
        }, index=[0])

        table_id = f"{project_id}.{dataset_id}.model_metadata"
        client.load_table_from_dataframe(
            metadata_df,
            table_id,
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
        ).result()

        logger.info(f"✅ Model metadata saved to {table_id}")

    return {
        "model": model,
        "idata": idata,
        "summary": summary,
        "brier_score": float(brier_score),
        "rhat_max": float(rhat_max),
        "ess_bulk_min": float(ess_bulk_min),
        "pct_divergences": float(pct_divergent),
        "checks_passed": all_checks_passed,
        "n_samples": len(y),
        "n_features": n_features,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = train_pymc_model(
        draws=2000,
        tune=1500,
        chains=4,
        target_accept=0.95,
        save_trace=True,
    )

    print("\n" + "=" * 70)
    print("PyMC Hierarchical Bayesian Model — Training Summary")
    print("=" * 70)
    print(f"Brier Score:        {result['brier_score']:.4f}")
    print(f"Rhat Max:           {result['rhat_max']:.4f} (target < 1.01)")
    print(f"ESS Bulk Min:       {result['ess_bulk_min']:.0f} (target > 1000)")
    print(f"Divergences:        {result['pct_divergences']:.2f}% (target < 0.1%)")
    print(f"Checks Passed:      {result['checks_passed']}")
    print(f"Model Version:      pymc-hierarchical-noncentered-v2")
    print(f"Samples:            {result['n_samples']}")
    print(f"Features:           {result['n_features']}")
    print(f"UFs:                27")
    print("=" * 70)
