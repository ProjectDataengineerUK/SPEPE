"""PyMC Electoral Model — non-centered hierarchical with historical + polls + candidate effects.

Architecture (3-level partial pooling):
  Level 1: UF intercept       a_uf[uf]      — partial pooling across 27 UFs
  Level 2: Candidate intercept gamma[cand]   — partial pooling (top-N + OTHER bucket)
  Level 3: Anchoring signals  w_hist, w_poll — pre-scaled [0,1] features with informative priors
  Level 4: Demo features       beta[uf,feat] — UF-varying slopes on IBGE z-scored features

Linear predictor (logit scale):
  eta = a_uf[uf]
      + gamma[cand]
      + w_hist * pct_votos_historico * (1 - is_new_candidate)
      + w_poll * media_intencao_voto_uf * (1 - poll_missing)
      + sum(beta_demo[uf] * X_demo, axis=1)

Likelihood: Beta(alpha=mu*kappa, beta=(1-mu)*kappa), mu = sigmoid(eta)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.pymc_electoral_model")

_TOP_N_CANDIDATES = 10


def prepare_electoral_features(
    df: pd.DataFrame,
    train_stats: dict | None = None,
) -> tuple[dict, dict]:
    """Prepare all features and group indices for the electoral model.

    Args:
        df: DataFrame from electoral_dataset_builder
        train_stats: If provided, apply stored normalization (for val/inference).
                     If None, compute from df (training mode).

    Returns:
        (features, stats) where:
          features: dict of numpy arrays ready for build_electoral_model()
          stats: normalization stats to store in norm_stats.json
    """
    demo_cols = ["log_populacao", "log_renda", "taxa_alfabetizacao", "taxa_analfabetismo"]

    # Log-transform right-skewed demographic features
    df = df.copy()
    df["log_populacao"] = np.log1p(df["populacao_total"].fillna(0))
    df["log_renda"] = np.log1p(df["renda_per_capita"].fillna(0))
    df["taxa_alfabetizacao"] = df["taxa_alfabetizacao"].fillna(50.0)
    df["taxa_analfabetismo"] = df["taxa_analfabetismo"].fillna(10.0)

    # Normalization
    if train_stats is None:
        train_mean = {c: float(df[c].mean()) for c in demo_cols}
        train_std = {c: float(df[c].std()) for c in demo_cols}
    else:
        train_mean = train_stats["demo_mean"]
        train_std = train_stats["demo_std"]

    X_demo = np.column_stack(
        [(df[c].values - train_mean[c]) / (train_std[c] + 1e-6) for c in demo_cols]
    )

    # UF encoding
    uf_cat = pd.Categorical(df["sg_uf"])
    uf_idx = uf_cat.codes
    uf_labels = list(uf_cat.categories)

    # Candidate grouping: top-N + OTHER
    if train_stats is None:
        top_cands = df["candidato"].value_counts().head(_TOP_N_CANDIDATES).index.tolist()
    else:
        top_cands = train_stats["top_candidates"]

    df["cand_grouped"] = df["candidato"].where(df["candidato"].isin(top_cands), "OTHER")
    cand_cat = pd.Categorical(df["cand_grouped"])
    cand_idx = cand_cat.codes
    cand_labels = list(cand_cat.categories)

    features = {
        "X_demo": X_demo,
        "hist": df["pct_votos_historico"].values.astype(float),
        "poll": df["media_intencao_voto_uf"].values.astype(float),
        "is_new": df["is_new_candidate"].values.astype(int),
        "poll_missing": df["poll_missing"].values.astype(int),
        "uf_idx": uf_idx,
        "cand_idx": cand_idx,
        "uf_labels": uf_labels,
        "cand_labels": cand_labels,
        "n_uf": len(uf_labels),
        "n_cand": len(cand_labels),
        "n_demo": len(demo_cols),
        "y": df["y"].values.astype(float),
    }

    stats = {
        "demo_cols": demo_cols,
        "demo_mean": train_mean,
        "demo_std": train_std,
        "uf_labels": uf_labels,
        "cand_labels": cand_labels,
        "top_candidates": top_cands,
    }

    return features, stats


def build_electoral_model(features: dict):
    """Build non-centered hierarchical PyMC electoral model.

    Model structure:
      - UF random intercept (non-centered, partial pooling across 27 UFs)
      - Candidate random intercept (non-centered, partial pooling top-N + OTHER)
      - w_hist: coefficient for historical vote share (strong informative prior, positive)
      - w_poll: coefficient for poll signal (informative prior, positive)
      - beta_demo: UF-varying slopes on 4 IBGE features (weakly informative)
      - kappa: Beta concentration (precision)
    """
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC required: pip install pymc arviz")

    n_uf = features["n_uf"]
    n_cand = features["n_cand"]
    n_demo = features["n_demo"]

    X_demo = features["X_demo"]
    hist = features["hist"]
    poll = features["poll"]
    is_new = features["is_new"]
    poll_missing = features["poll_missing"]
    uf_idx = features["uf_idx"]
    cand_idx = features["cand_idx"]
    y = features["y"]

    with pm.Model() as model:
        # ── MutableData for val/inference via pm.set_data ────────────────────
        X_data = pm.MutableData("X_data", X_demo)
        hist_data = pm.MutableData("hist_data", hist)
        poll_data = pm.MutableData("poll_data", poll)
        is_new_data = pm.MutableData("is_new_data", is_new)
        poll_miss = pm.MutableData("poll_missing_data", poll_missing)
        uf_data = pm.MutableData("uf_idx_data", uf_idx)
        cand_data = pm.MutableData("cand_idx_data", cand_idx)

        # ── Level 1: UF intercept (non-centered) ────────────────────────────
        mu_uf = pm.Normal("mu_uf", 0.0, 1.0)
        sd_uf = pm.HalfNormal("sd_uf", 0.5)
        a_raw = pm.Normal("a_raw", 0.0, 1.0, shape=n_uf)
        a_uf = pm.Deterministic("a_uf", mu_uf + sd_uf * a_raw)

        # ── Level 2: Candidate intercept (non-centered) ──────────────────────
        mu_cand = pm.Normal("mu_cand", 0.0, 1.0)
        sd_cand = pm.HalfNormal("sd_cand", 0.5)
        g_raw = pm.Normal("g_raw", 0.0, 1.0, shape=n_cand)
        gamma = pm.Deterministic("gamma", mu_cand + sd_cand * g_raw)

        # ── Level 3: Anchoring signals (informative priors, positive) ────────
        # w_hist ~ N(4, 1): on logit scale, Δ0.2 historical → Δ0.8 logit
        # w_poll ~ N(3, 1): polls are noisier at municipal level
        w_hist = pm.Normal("w_hist", mu=4.0, sigma=1.0)
        w_poll = pm.Normal("w_poll", mu=3.0, sigma=1.0)

        # ── Level 4: UF-varying demo slopes (weakly informative) ────────────
        beta_demo = pm.Normal("beta_demo", 0.0, 0.5, shape=(n_uf, n_demo))

        # ── Linear predictor ─────────────────────────────────────────────────
        eta = (
            a_uf[uf_data]
            + gamma[cand_data]
            + w_hist * hist_data * (1 - is_new_data)
            + w_poll * poll_data * (1 - poll_miss)
            + (X_data * beta_demo[uf_data]).sum(axis=1)
        )
        mu = pm.Deterministic("mu", pm.math.sigmoid(eta))

        # ── Beta likelihood ──────────────────────────────────────────────────
        # kappa (concentration): HalfNormal(50) → prior mean ≈ 40 → var ≈ 0.006 at mu=0.2
        kappa = pm.HalfNormal("kappa", sigma=50.0)
        pm.Beta("y_obs", alpha=mu * kappa, beta=(1 - mu) * kappa, observed=y)

    return model


def sample_electoral_posterior(
    model,
    draws: int = 1000,
    tune: int = 2000,
    chains: int = 4,
    target_accept: float = 0.95,
    random_seed: int = 42,
):
    """Sample from electoral model posterior using NUTS."""
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC required: pip install pymc arviz")

    with model:
        idata = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            target_accept=target_accept,
            init="jitter+adapt_diag",
            random_seed=random_seed,
            cores=chains,
            return_inferencedata=True,
            progressbar=True,
        )
    return idata
