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
    draws: int = 1000,
    tune: int = 500,
    chains: int = 2,
    target_accept: float = 0.9,
    save_trace: bool = True,
) -> dict:
    """Train hierarchical Bayesian logistic model for electoral prediction.

    Model structure:
    - Intercept (alpha) varies by UF (state-level effects)
    - Slopes (beta) vary by UF × feature interactions
    - Prior: weakly informative Normal distributions
    - Likelihood: Bernoulli(logistic(X*beta + alpha))

    Args:
        project_id: GCP project
        dataset_id: BigQuery dataset for training_dataset
        draws: MCMC draws per chain (default 1000)
        tune: tuning/burn-in steps (default 500)
        chains: parallel chains (default 2)
        target_accept: NUTS target acceptance rate (default 0.9)
        save_trace: save posterior samples to BigQuery

    Returns:
        Dict with model metrics and sample quality
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
    feature_cols = [
        "populacao",
        "densidade_populacional",
        "renda_media",
        "pct_ensino_superior",
        "pct_analfabetos",
        "taxa_desemprego",
        "sentimento_positivo",
        "sentimento_negativo",
        "polarizacao_entropia",
        "mencoes_sociais",
        "cobertura_sus",
        "mortalidade_infantil",
        "taxa_homicidio",
        "taxa_roubo",
    ]

    # Normalize features (z-score)
    df_features = df[feature_cols].fillna(0)
    df_features_norm = (df_features - df_features.mean()) / (df_features.std() + 1e-6)

    # Use continuous target: pct_votos (0-1) com link logit
    # Regressão logística contínua preserva informação vs binary classification
    df["y_continuous"] = df["pct_votos"].clip(0.001, 0.999)  # evitar log(0) e log(1)

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

    with pm.Model() as model:
        # Hyperpriors: population-level effect distributions
        mu_alpha = pm.Normal("mu_alpha", mu=0, sigma=1)
        sigma_alpha = pm.HalfNormal("sigma_alpha", sigma=1)

        mu_beta = pm.Normal("mu_beta", mu=0, sigma=1, shape=n_features)
        sigma_beta = pm.HalfNormal("sigma_beta", sigma=1, shape=n_features)

        # Group-level effects (vary by UF)
        alpha = pm.Normal("alpha", mu=mu_alpha, sigma=sigma_alpha, shape=n_uf)
        beta = pm.Normal(
            "beta",
            mu=mu_beta[None, :],
            sigma=sigma_beta[None, :],
            shape=(n_uf, n_features),
        )

        # Linear predictor + sigmoid para proporções (0-1)
        X_tensor = pt.as_tensor_variable(X)
        logit_p = alpha[uf_groups] + pm.math.dot(X_tensor, beta[uf_groups].T).diagonal()
        p = pm.Deterministic("p", pm.math.sigmoid(logit_p))

        # Likelihood: Beta distribution para proporções (mais apropriado que Bernoulli)
        # mu=p, kappa=10 (concentração) → adequado para dados proporção
        pm.Beta("y_obs", alpha=p * 10, beta=(1 - p) * 10, observed=y_obs)

    logger.info("Model compiled successfully")

    # ── Step 4: Sample from posterior ──────────────────────────────────────
    logger.info(f"Sampling {draws} draws × {chains} chains (tune={tune})...")
    result = sample_posterior(
        model,
        draws=draws,
        tune=tune,
        chains=chains,
        target_accept=target_accept,
    )

    trace = result["trace"]
    summary = result["summary"]

    logger.info("Sampling complete")
    logger.info(f"Rhat max: {result['r_hat_max']:.4f}")
    logger.info(f"ESS bulk min: {result['ess_bulk_min']:.0f}")

    # ── Step 5: Model quality checks ────────────────────────────────────────
    rhat_max = result["r_hat_max"]
    ess_bulk_min = result["ess_bulk_min"]

    checks = {
        "rhat_converged": rhat_max < 1.01,
        "ess_sufficient": ess_bulk_min > 400,
        "chains_ok": result.get("divergence_count", 0) == 0,
    }

    all_checks_passed = all(checks.values())
    logger.info(f"Quality checks: {checks}")

    if not all_checks_passed:
        logger.warning("⚠️  Some quality checks failed. Consider resampling with more draws/tune.")
    else:
        logger.info("✅ All quality checks passed")

    # ── Step 6: Generate predictions on validation set ──────────────────────
    logger.info("Generating predictions...")

    # Posterior predictive for validation
    with model:
        ppc = pm.sample_posterior_predictive(trace, random_seed=42)

    y_pred_proba = ppc.posterior_predictive["y_obs"].mean(axis=0).mean(axis=0)

    # Compute Brier score
    brier_score = np.mean((y_pred_proba - y) ** 2)
    logger.info(f"Brier score (validation): {brier_score:.4f}")

    # ── Step 7: Save trace to BigQuery ─────────────────────────────────────
    if save_trace:
        logger.info("Saving trace to BigQuery...")

        client = bigquery.Client(project=project_id)

        # Flatten posterior samples for storage
        trace_df = pd.DataFrame({
            "model_version": "pymc-hierarchical-v1",
            "timestamp": pd.Timestamp.utcnow(),
            "n_draws": draws,
            "n_tune": tune,
            "n_chains": chains,
            "rhat_max": float(rhat_max),
            "ess_bulk_min": float(ess_bulk_min),
            "brier_score": float(brier_score),
            "all_checks_passed": bool(all_checks_passed),
            "feature_count": n_features,
            "uf_count": n_uf,
            "sample_count": len(y),
        }, index=[0])

        table_id = f"{project_id}.{dataset_id}.model_metadata"
        client.load_table_from_dataframe(
            trace_df,
            table_id,
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
        ).result()

        logger.info(f"✅ Trace metadata saved to {table_id}")

    return {
        "model": model,
        "trace": trace,
        "summary": summary,
        "brier_score": float(brier_score),
        "rhat_max": float(rhat_max),
        "ess_bulk_min": float(ess_bulk_min),
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
        draws=1000,
        tune=500,
        chains=2,
        target_accept=0.9,
        save_trace=True,
    )

    print("\n" + "=" * 60)
    print("PyMC Training Summary")
    print("=" * 60)
    print(f"Brier Score: {result['brier_score']:.4f}")
    print(f"Rhat Max: {result['rhat_max']:.4f}")
    print(f"ESS Bulk Min: {result['ess_bulk_min']:.0f}")
    print(f"Checks Passed: {result['checks_passed']}")
    print(f"Samples: {result['n_samples']}")
    print(f"Features: {result['n_features']}")
    print("=" * 60)
