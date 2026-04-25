"""PyMC Hierarchical Logistic Model for electoral prediction (production).

This module implements the full Bayesian hierarchical model that replaces
the bootstrap approximation in production. Requires PyMC >= 5.0.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.pymc")


def build_hierarchical_model(
    df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    group_col: str = "sg_uf",
):
    """Build a PyMC hierarchical logistic model grouped by UF."""
    try:
        import pymc as pm
        import pytensor.tensor as pt
    except ImportError:
        raise ImportError(
            "PyMC não instalado. Execute: pip install pymc\n"
            "Para o MVP, use mlops.components.train_bootstrap.predict_with_ic() em vez desta função."
        )

    X = df[feature_cols].fillna(0).values
    y = df[target_col].values.astype(int)
    groups = pd.Categorical(df[group_col]).codes
    n_groups = len(df[group_col].unique())
    n_features = X.shape[1]

    with pm.Model() as model:
        mu_alpha = pm.Normal("mu_alpha", mu=0, sigma=1)
        sigma_alpha = pm.HalfNormal("sigma_alpha", sigma=1)
        alpha = pm.Normal("alpha", mu=mu_alpha, sigma=sigma_alpha, shape=n_groups)

        mu_beta = pm.Normal("mu_beta", mu=0, sigma=1, shape=n_features)
        sigma_beta = pm.HalfNormal("sigma_beta", sigma=1)
        beta = pm.Normal("beta", mu=mu_beta, sigma=sigma_beta, shape=(n_groups, n_features))

        X_tensor = pt.as_tensor_variable(X)
        logit_p = alpha[groups] + pm.math.dot(X_tensor, beta[groups].T).diagonal()
        p = pm.Deterministic("p", pm.math.sigmoid(logit_p))

        pm.Bernoulli("y_obs", p=p, observed=y)

    return model


def sample_posterior(
    model,
    draws: int = 1000,
    tune: int = 500,
    chains: int = 2,
    target_accept: float = 0.9,
) -> dict:
    """Sample from the posterior using NUTS sampler."""
    try:
        import pymc as pm
        import arviz as az
    except ImportError:
        raise ImportError("PyMC e ArviZ necessários: pip install pymc arviz")

    with model:
        trace = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            target_accept=target_accept,
            progressbar=True,
        )

    summary = az.summary(trace, var_names=["p"])
    return {
        "trace": trace,
        "summary": summary,
        "r_hat_max": float(summary["r_hat"].max()),
        "ess_bulk_min": float(summary["ess_bulk"].min()),
    }


def predict_pymc(
    trace,
    X_new: np.ndarray,
    group_idx: int = 0,
) -> dict:
    """Generate predictions from PyMC posterior samples."""
    try:
        import numpy as np
    except ImportError:
        raise ImportError("PyMC necessário.")

    alpha_samples = trace.posterior["alpha"].values.reshape(-1, trace.posterior["alpha"].shape[-1])[:, group_idx]
    beta_samples = trace.posterior["beta"].values.reshape(-1, *trace.posterior["beta"].shape[-2:])[:, group_idx, :]

    logits = alpha_samples + beta_samples @ X_new
    probs = 1 / (1 + np.exp(-logits))

    return {
        "point_estimate": float(probs.mean()),
        "ci_lower": float(np.percentile(probs, 2.5)),
        "ci_upper": float(np.percentile(probs, 97.5)),
        "hdi_lower": float(np.percentile(probs, 5)),
        "hdi_upper": float(np.percentile(probs, 95)),
        "n_samples": len(probs),
        "method": "pymc_hlm",
    }
