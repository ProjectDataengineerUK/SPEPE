"""PyMC Hierarchical Logistic Model for electoral prediction (production).

This module implements a non-centered hierarchical Bayesian model with learned
dispersion for robust electoral prediction. Requires PyMC >= 5.0.

Architecture:
- Non-centered parameterization: mu/sigma splits from raw effects (avoids Neal's funnel)
- Intercept (mu_a): population mean, s_a: variation across UFs
- Slopes (mu_b): feature-level population means, s_b: feature-level variation
- Dispersion (phi): learned Beta concentration parameter (Gamma prior)
- Likelihood: Beta(alpha=p*phi, beta=(1-p)*phi) for proportion targets
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("spepe.mlops.pymc")


def build_hierarchical_model(
    X: np.ndarray,
    y: np.ndarray,
    uf_idx: np.ndarray,
    n_uf: int,
    n_features: int,
):
    """Build a non-centered PyMC hierarchical Bayesian model for electoral prediction.

    Args:
        X: Feature matrix (N, n_features), normalized (z-score)
        y: Target vector (N,), continuous proportions in [0, 1]
        uf_idx: UF group indices (N,), integers 0..n_uf-1
        n_uf: Number of UF groups (27 for Brasil)
        n_features: Number of features (11 for electoral model)

    Returns:
        PyMC Model context manager with full hierarchy defined

    Structure:
    - Hyperpriors: mu_a, s_a for intercepts; mu_b, s_b for slopes
    - Raw effects: a_raw, b_raw (unit normal, scaled by s_a/s_b)
    - Non-centered construction: alpha = mu_a + s_a*a_raw, beta = mu_b + s_b*b_raw
    - Linear predictor: logit_p = alpha[uf_idx] + X @ beta[uf_idx]
    - Learned dispersion: phi ~ Gamma(2, 0.1)
    - Likelihood: Beta with alpha=p*phi, beta=(1-p)*phi
    """
    try:
        import pymc as pm
        import pytensor.tensor as pt
    except ImportError:
        raise ImportError(
            "PyMC não instalado. Execute: pip install pymc pytensor\n"
            "Para o MVP, use mlops.components.train_bootstrap.predict_with_ic() em vez desta função."
        )

    # Validate inputs
    assert X.shape[0] == len(y), "X and y must have same number of samples"
    assert X.shape[1] == n_features, "X must have n_features columns"
    assert len(uf_idx) == len(y), "uf_idx must match y length"
    assert np.all((uf_idx >= 0) & (uf_idx < n_uf)), "uf_idx must be in [0, n_uf)"
    assert np.all((y > 0) & (y < 1)), "y must be proportions in (0, 1)"

    with pm.Model() as model:
        # ── Hyperpriors: Intercepts (population mean + variation) ──────────────
        mu_a = pm.Normal("mu_a", mu=0, sigma=1)
        s_a = pm.HalfNormal("s_a", sigma=1)

        # ── Hyperpriors: Slopes (feature-aware domain priors) ──────────────────
        # Order matches training_dataset_builder: populacao, densidade, renda, ensino,
        # analfabetos, desemprego, sentimento_pos, sentimento_neg, polarizacao,
        # cobertura_sus, mortalidade
        feature_sigma = np.array(
            [
                1.0,  # populacao
                0.5,  # densidade_populacional
                1.0,  # renda_media
                0.3,  # pct_ensino_superior
                0.3,  # pct_analfabetos
                0.2,  # taxa_desemprego
                0.2,  # sentimento_positivo
                0.5,  # sentimento_negativo
                0.7,  # polarizacao_entropia
                0.3,  # cobertura_sus
                0.8,  # mortalidade_infantil
            ]
        )

        mu_b = pm.Normal("mu_b", mu=0, sigma=feature_sigma, shape=n_features)
        s_b = pm.HalfNormal("s_b", sigma=0.5, shape=n_features)

        # ── Raw effects (unit normal, will be scaled by hyperpriors) ───────────
        # Non-centered: these are N(0,1), then multiplied by s_a / s_b
        a_raw = pm.Normal("a_raw", mu=0, sigma=1, shape=n_uf)
        b_raw = pm.Normal("b_raw", mu=0, sigma=1, shape=(n_uf, n_features))

        # ── Non-centered construction ──────────────────────────────────────────
        # This avoids the "funnel" where posterior correlation between hyperpriors
        # and raw effects causes NUTS sampler to struggle
        alpha = pm.Deterministic("alpha", mu_a + s_a * a_raw)
        beta = pm.Deterministic("beta", mu_b[None, :] + s_b[None, :] * b_raw)

        # ── Linear predictor + sigmoid ─────────────────────────────────────────
        X_tensor = pt.as_tensor_variable(X)
        eta = alpha[uf_idx] + (X_tensor * beta[uf_idx]).sum(axis=1)
        p = pm.Deterministic("p", pm.math.sigmoid(eta))

        # ── Learned dispersion (concentration for Beta likelihood) ─────────────
        # Gamma(2, 0.1) → E[phi]=20, but with high variance for flexibility
        phi = pm.Gamma("phi", alpha=2, beta=0.1)

        # ── Likelihood: Beta distribution for proportions ────────────────────
        # More appropriate than Bernoulli for continuous targets in [0,1]
        # alpha=p*phi, beta=(1-p)*phi ensures E[y]=p and concentration ~ phi
        pm.Beta("y_obs", alpha=p * phi, beta=(1 - p) * phi, observed=y)

    return model


def sample_posterior(
    model,
    draws: int = 2000,
    tune: int = 1500,
    chains: int = 4,
    target_accept: float = 0.95,
    init: str = "jitter+adapt_diag",
    random_seed: int = 42,
) -> object:
    """Sample from posterior using NUTS sampler with robust diagnostics.

    Args:
        model: PyMC model to sample from
        draws: MCMC draws per chain (default 2000, increased from 1000)
        tune: Tuning/burn-in steps (default 1500, increased from 500)
        chains: Number of parallel chains (default 4, increased from 2)
        target_accept: NUTS target acceptance rate (default 0.95, increased from 0.9)
        init: Initialization strategy (default "jitter+adapt_diag")
        random_seed: Random seed for reproducibility

    Returns:
        InferenceData object (ArviZ native format) with full posterior samples

    Notes:
        - 4 chains required for reliable Rhat diagnostics (< 1.01)
        - 2000 draws per chain gives ESS_bulk > 1000 for typical models
        - target_accept=0.95 reduces divergences (more conservative)
        - jitter+adapt_diag initialization reduces initial divergences
    """
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC necessário: pip install pymc")

    with model:
        idata = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            target_accept=target_accept,
            init=init,
            random_seed=random_seed,
            cores=4,
            return_inferencedata=True,
            progressbar=True,
        )

    return idata


def predict_pymc(
    idata,
    X_new: np.ndarray,
    group_idx: int = 0,
) -> dict:
    """Generate predictions from PyMC posterior samples (InferenceData).

    Args:
        idata: ArviZ InferenceData object from sample_posterior()
        X_new: Feature vector (n_features,), normalized
        group_idx: UF index for group-specific prediction

    Returns:
        Dict with point estimate, credible interval, and diagnostics
    """
    # Extract posterior samples: p[draw, chain, obs] → reshape to (n_samples, n_obs)
    p_samples = idata.posterior["p"].values.reshape(-1)

    # For predictions, use posterior mean as point estimate
    point_estimate = float(p_samples.mean())
    ci_lower = float(np.percentile(p_samples, 2.5))
    ci_upper = float(np.percentile(p_samples, 97.5))
    hdi_lower = float(np.percentile(p_samples, 5))
    hdi_upper = float(np.percentile(p_samples, 95))

    return {
        "point_estimate": point_estimate,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "hdi_lower": hdi_lower,
        "hdi_upper": hdi_upper,
        "n_samples": len(p_samples),
        "method": "pymc_hierarchical_nc",
    }
