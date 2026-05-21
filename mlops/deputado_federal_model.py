"""Modelo de previsão para Deputado Federal — proporcional D'Hondt (RJ 2026).

Arquitetura em 2 etapas:
  Etapa 1 — Previsão individual de votos
    Modelo log-normal hierárquico: log(votos_cand) ~ Normal(eta, sigma_cand)
    Features (blocos F1-F5 do FEATURE_SPEC):
      F1: log_votos_historico_2022, incumbente_federal
      F2: posicao_estimada_nominata, forca_nominata, cadeiras_estimadas_partido
      F3: indice_concentracao_top5, capilaridade_municipios
      F4: apoios_prefeitos_ponderados (soma eleitorado municípios apoiantes)
      F5: ig_followers_log, yt_subscribers_log, x_followers_log

  Etapa 2 — Alocação D'Hondt (Monte Carlo)
    Para cada simulação:
      - Amostra votos_cand ~ LogNormal(eta, sigma)
      - Agrega votos por partido/federação
      - Executa D'Hondt com quociente eleitoral (art. 106)
      - Conta eleitos → probabilidade_eleicao = freq / n_simulacoes

Output: DeputadoResult com prob_eleicao, votos_medio, IC90% por candidato

Integração com mlops.dhondt_simulator:
    from mlops.dhondt_simulator import DHondtSimulator, build_nominata_features
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mlops.dhondt_simulator import DHondtSimulator, MonteCarloResult

logger = logging.getLogger("spepe.mlops.deputado_federal_model")

N_CADEIRAS_RJ = 46


@dataclass
class DeputadoResult:
    """Resultado completo do modelo M-F."""
    candidatos: list[str]
    partidos: dict[str, str]
    prob_eleicao: dict[str, float]
    votos_medio: dict[str, float]
    votos_p10: dict[str, float]
    votos_p90: dict[str, float]
    cadeiras_medio_partido: dict[str, float]
    score_viabilidade: dict[str, float]
    n_simulacoes: int
    monte_carlo_raw: MonteCarloResult | None = field(default=None, repr=False)

    def to_df(self) -> pd.DataFrame:
        rows = [
            {
                "candidato": c,
                "partido": self.partidos.get(c, ""),
                "prob_eleicao": self.prob_eleicao.get(c, 0.0),
                "votos_medio": self.votos_medio.get(c, 0.0),
                "votos_p10": self.votos_p10.get(c, 0.0),
                "votos_p90": self.votos_p90.get(c, 0.0),
                "score_viabilidade": self.score_viabilidade.get(c, 0.0),
            }
            for c in self.candidatos
        ]
        return (
            pd.DataFrame(rows)
            .sort_values("prob_eleicao", ascending=False)
            .reset_index(drop=True)
        )


# ── Feature preparation ────────────────────────────────────────────────────────


def prepare_deputado_features(
    df: pd.DataFrame,
    train_stats: dict | None = None,
) -> tuple[dict, dict]:
    """Prepare features for the deputado federal model (log-normal vote prediction).

    Expected columns in df:
      Obrigatórias:
        candidato, partido, votos_2022 (or votos_historico), y_log_votos
      Opcionais (zeradas se ausentes):
        incumbente_federal, posicao_estimada_nominata, forca_nominata,
        cadeiras_estimadas_partido, indice_concentracao_top5,
        capilaridade_municipios, apoios_prefeitos_ponderados,
        ig_followers, yt_subscribers, x_followers

    Returns:
        (features, stats) where features feeds build_deputado_model()
    """
    feat_cols = [
        "log_votos_hist",
        "incumbente_federal",
        "posicao_estimada_nominata_norm",
        "forca_nominata",
        "cadeiras_estimadas_partido_norm",
        "indice_concentracao_top5",
        "capilaridade_municipios",
        "apoios_prefeitos_ponderados",
        "ig_followers_log",
        "yt_subscribers_log",
        "x_followers_log",
    ]

    df = df.copy()
    votos_hist = df.get("votos_historico", df.get("votos_2022", pd.Series(0, index=df.index)))
    df["log_votos_hist"] = np.log1p(votos_hist.fillna(0))

    df["incumbente_federal"] = df["incumbente_federal"].fillna(0).astype(float) if "incumbente_federal" in df else 0.0

    for col in ("posicao_estimada_nominata", "cadeiras_estimadas_partido"):
        norm_col = col + "_norm"
        if col in df:
            max_val = df[col].max() or 1
            df[norm_col] = df[col].fillna(0) / max_val
        else:
            df[norm_col] = 0.0

    for col in (
        "forca_nominata", "indice_concentracao_top5",
        "capilaridade_municipios", "apoios_prefeitos_ponderados",
    ):
        if col not in df:
            df[col] = 0.0
        else:
            df[col] = df[col].fillna(0.0)

    for src, dst in (("ig_followers", "ig_followers_log"), ("yt_subscribers", "yt_subscribers_log"), ("x_followers", "x_followers_log")):
        df[dst] = np.log1p(df[src].fillna(0)) if src in df else 0.0

    if train_stats is None:
        feat_mean = {c: float(df[c].mean()) for c in feat_cols}
        feat_std = {c: float(df[c].std()) for c in feat_cols}
    else:
        feat_mean = train_stats["feat_mean"]
        feat_std = train_stats["feat_std"]

    X = np.column_stack(
        [(df[c].values - feat_mean[c]) / (feat_std[c] + 1e-6) for c in feat_cols]
    )

    # Partido grouping (federações como entidade única)
    partido_series = df.get("partido", pd.Series("OUTROS", index=df.index))
    partido_cat = pd.Categorical(partido_series.fillna("OUTROS"))
    partido_idx = partido_cat.codes
    partido_labels = list(partido_cat.categories)

    if "y_log_votos" not in df:
        if "votos_historico" in df:
            df["y_log_votos"] = np.log1p(df["votos_historico"].fillna(0))
        else:
            df["y_log_votos"] = np.zeros(len(df))

    features = {
        "X": X,
        "partido_idx": partido_idx,
        "partido_labels": partido_labels,
        "n_partido": len(partido_labels),
        "n_feat": X.shape[1],
        "feat_cols": feat_cols,
        "y": df["y_log_votos"].values.astype(float),
        "candidatos": df["candidato"].tolist(),
        "partidos": dict(zip(df["candidato"], partido_series.fillna("OUTROS"))),
    }

    stats = {
        "feat_cols": feat_cols,
        "feat_mean": feat_mean,
        "feat_std": feat_std,
        "partido_labels": partido_labels,
    }

    return features, stats


# ── PyMC log-normal vote model ─────────────────────────────────────────────────


def build_deputado_model(features: dict):
    """Build hierarchical PyMC log-normal model for individual vote prediction.

    eta = mu_global
        + alpha_partido[partido]
        + sum(beta[feat] * X)

    log(votos) ~ Normal(eta, sigma_residual)

    Priors:
      mu_global: Normal(0, 2) — global log-votos intercept
      alpha_partido: partially-pooled across partidos (non-centered)
      beta: Normal(0, 1) per feature
      sigma: HalfNormal(1) — residual noise
    """
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC required: pip install pymc arviz")

    n_partido = features["n_partido"]
    n_feat = features["n_feat"]

    with pm.Model() as model:
        X_data = pm.MutableData("X_data", features["X"])
        partido_data = pm.MutableData("partido_idx_data", features["partido_idx"])

        # ── Global intercept ─────────────────────────────────────────────────
        mu_global = pm.Normal("mu_global", 0.0, 2.0)

        # ── Partido random effects (partial pooling, non-centered) ───────────
        sd_partido = pm.HalfNormal("sd_partido", 1.0)
        a_raw = pm.Normal("a_raw", 0.0, 1.0, shape=n_partido)
        alpha_partido = pm.Deterministic("alpha_partido", sd_partido * a_raw)

        # ── Feature coefficients ─────────────────────────────────────────────
        beta = pm.Normal("beta", 0.0, 1.0, shape=n_feat)

        # ── Linear predictor (log-votos scale) ───────────────────────────────
        eta = mu_global + alpha_partido[partido_data] + (X_data * beta).sum(axis=1)

        # ── Residual noise ────────────────────────────────────────────────────
        sigma = pm.HalfNormal("sigma", 1.0)
        pm.Normal("y_obs", mu=eta, sigma=sigma, observed=features["y"])

    return model


def sample_deputado_posterior(
    model,
    draws: int = 1000,
    tune: int = 2000,
    chains: int = 4,
    target_accept: float = 0.90,
    random_seed: int = 42,
):
    """Sample deputado model posterior. Lower target_accept (0.90) for simpler geometry."""
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


# ── D'Hondt Monte Carlo integration ───────────────────────────────────────────


def predict_deputados(
    model,
    idata,
    features: dict,
    n_simulacoes: int = 10_000,
    n_cadeiras: int = N_CADEIRAS_RJ,
    seed: int = 42,
) -> DeputadoResult:
    """Generate full D'Hondt Monte Carlo election simulation from PyMC posterior.

    Uses posterior samples of eta + sigma to draw vote scenarios, then runs
    D'Hondt for each scenario to estimate election probabilities.

    Returns DeputadoResult with prob_eleicao, votos_medio, IC90% and score_viabilidade.
    """
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC required: pip install pymc arviz")

    candidatos = features["candidatos"]
    partidos = features["partidos"]

    # Extract posterior eta samples (mu predictions)
    with model:
        pm.set_data({"X_data": features["X"], "partido_idx_data": features["partido_idx"]})
        ppc = pm.sample_posterior_predictive(idata, var_names=["y_obs"], progressbar=False)

    y_samples = ppc.posterior_predictive["y_obs"].values  # (chain, draw, n_cand)
    y_flat = y_samples.reshape(-1, y_samples.shape[-1])  # (samples, n_cand)

    total_samples = y_flat.shape[0]
    rng = np.random.default_rng(seed)
    sim_indices = rng.integers(0, total_samples, size=n_simulacoes)

    sim = DHondtSimulator(n_cadeiras=n_cadeiras)
    eleicoes: dict[str, int] = {c: 0 for c in candidatos}

    for idx in sim_indices:
        log_votos = y_flat[idx]
        votos = np.maximum(0, np.round(np.expm1(log_votos))).astype(int)
        votos_dict = dict(zip(candidatos, votos.tolist()))
        result = sim.simulate(votos_dict, partidos)
        for eleito in result.eleitos:
            eleicoes[eleito] = eleicoes.get(eleito, 0) + 1

    prob = {c: eleicoes[c] / n_simulacoes for c in candidatos}
    votos_arr = np.expm1(y_flat)

    mc = MonteCarloResult(
        n_simulacoes=n_simulacoes,
        probabilidade_eleicao=prob,
        votos_medio={c: float(votos_arr[:, i].mean()) for i, c in enumerate(candidatos)},
        votos_p10={c: float(np.percentile(votos_arr[:, i], 10)) for i, c in enumerate(candidatos)},
        votos_p90={c: float(np.percentile(votos_arr[:, i], 90)) for i, c in enumerate(candidatos)},
        cadeiras_medio_por_partido={},
    )

    # Score de viabilidade composto (F1-F5)
    X = features["X"]
    feat_cols = features["feat_cols"]

    def _get_feat(name: str) -> np.ndarray:
        if name in feat_cols:
            return X[:, feat_cols.index(name)]
        return np.zeros(len(candidatos))

    def _normalize(arr: np.ndarray) -> np.ndarray:
        vmin, vmax = arr.min(), arr.max()
        if vmax == vmin:
            return np.zeros_like(arr)
        return (arr - vmin) / (vmax - vmin)

    score = (
        0.25 * _normalize(_get_feat("log_votos_hist"))
        + 0.25 * _normalize(_get_feat("forca_nominata"))
        + 0.20 * _normalize(_get_feat("apoios_prefeitos_ponderados"))
        + 0.15 * _normalize(_get_feat("capilaridade_municipios"))
        + 0.10 * _normalize(
            _get_feat("ig_followers_log") + _get_feat("yt_subscribers_log")
        )
        + 0.05 * (1 - _normalize(_get_feat("incumbente_federal")) * 0)
    )

    score_dict = {c: round(float(score[i]), 4) for i, c in enumerate(candidatos)}

    return DeputadoResult(
        candidatos=candidatos,
        partidos=partidos,
        prob_eleicao=prob,
        votos_medio=mc.votos_medio,
        votos_p10=mc.votos_p10,
        votos_p90=mc.votos_p90,
        cadeiras_medio_partido=mc.cadeiras_medio_por_partido,
        score_viabilidade=score_dict,
        n_simulacoes=n_simulacoes,
        monte_carlo_raw=mc,
    )


def simulate_deputados_from_estimates(
    votos_media: dict[str, float],
    partido_por_candidato: dict[str, str],
    votos_std: dict[str, float] | None = None,
    n_simulacoes: int = 10_000,
    n_cadeiras: int = N_CADEIRAS_RJ,
    seed: int = 42,
) -> DeputadoResult:
    """Convenience function: run D'Hondt Monte Carlo directly from vote estimates.

    Skips PyMC — useful for quick scenario analysis or when posterior is not available.
    Uses Gaussian noise: votos ~ Normal(votos_media, votos_std).

    Args:
        votos_media: estimated votes per candidate (e.g. from historical scaling)
        partido_por_candidato: candidate → party mapping
        votos_std: std dev per candidate; defaults to 20% of votos_media
        n_simulacoes: Monte Carlo iterations
        n_cadeiras: seats to allocate (46 for RJ federal)
        seed: reproducibility seed
    """
    sim = DHondtSimulator(n_cadeiras=n_cadeiras)
    mc = sim.monte_carlo(
        votos_media=votos_media,
        partido_por_candidato=partido_por_candidato,
        votos_std=votos_std,
        n_simulacoes=n_simulacoes,
        seed=seed,
    )

    candidatos = list(votos_media.keys())
    score = {c: round(mc.votos_medio.get(c, 0) / (max(mc.votos_medio.values()) + 1), 4) for c in candidatos}

    return DeputadoResult(
        candidatos=candidatos,
        partidos=partido_por_candidato,
        prob_eleicao=mc.probabilidade_eleicao,
        votos_medio=mc.votos_medio,
        votos_p10=mc.votos_p10,
        votos_p90=mc.votos_p90,
        cadeiras_medio_partido=mc.cadeiras_medio_por_partido,
        score_viabilidade=score,
        n_simulacoes=n_simulacoes,
        monte_carlo_raw=mc,
    )
