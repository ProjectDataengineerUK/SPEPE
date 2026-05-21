"""PyMC Governador Model — modelo hierárquico para eleição majoritária (RJ 2026).

Diferenças do pymc_electoral_model (genérico/proporcional):
  - Foco RJ: 8 macrorregiões (Serra, Norte, Noroeste, Baixadas, Médio Paraíba,
             Lagos, Costa Verde, Metropolitana) substituem UFs como agrupamento.
  - Features de rejeição (G1): taxa_rejeicao, potencial_teorico, saldo_intencao_rejeicao.
  - Apoios estruturais (G4): apoios_prefeitos_ponderados (eleitorado ponderado).
  - Candidatos: ~5-10 (corrida majoritária) vs. 100+ (proporcional).
  - Likelihood: Beta em pct_votos_validos (0,1); kappa calibrado para corrida
    com 4-8 candidatos (pct_votos esperado 15-50% para líder).

Linear predictor (logit):
  eta = a_regiao[reg]
      + gamma[cand]
      + w_hist * pct_votos_historico * (1 - is_new)
      + w_poll * media_intencao_voto * (1 - poll_missing)
      - w_rej  * taxa_rejeicao
      + w_apoio * apoios_prefeitos_ponderados
      + w_saldo * saldo_intencao_rejeicao
      + sum(beta_demo[reg] * X_demo)

Prior calibration (4-8 candidatos, pct_votos ~ 15-50%):
  w_hist: TruncatedNormal(mu=2.0, sigma=0.5, lower=0)
    → hist=0.40 → contrib 0.8 → sigmoid(0.8)≈0.69 (plausível para líder)
  w_poll: TruncatedNormal(mu=2.5, sigma=0.5, lower=0)
    → poll=0.45 → contrib 1.1 → indica força da pesquisa em corrida binária
  w_rej:  TruncatedNormal(mu=1.0, sigma=0.5, lower=0)
    → rejeição alta penaliza mesmo candidatos com poll alto (fenômeno Witzel)
  w_apoio: TruncatedNormal(mu=0.5, sigma=0.3, lower=0)
    → apoio de prefeitos pondera por eleitorado (normalizado)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.pymc_governador_model")

# 8 macrorregiões de planejamento do RJ (CEPERJ/SEPLAG)
RJ_MACRORREGIOES = {
    "Metropolitana": [
        "Rio de Janeiro",
        "Duque de Caxias",
        "Nova Iguaçu",
        "São Gonçalo",
        "Niterói",
        "Belford Roxo",
        "Mesquita",
        "Nilópolis",
        "São João de Meriti",
        "Queimados",
        "Itaguaí",
        "Seropédica",
        "Maricá",
        "Magé",
        "Japeri",
        "Paracambi",
        "Guapimirim",
        "Tanguá",
        "Cachoeiras de Macacu",
        "Rio Bonito",
    ],
    "Serra": [
        "Petrópolis",
        "Teresópolis",
        "Nova Friburgo",
        "Sumidouro",
        "Bom Jardim",
        "Cantagalo",
        "Cordeiro",
        "Duas Barras",
        "Macuco",
        "Santa Maria Madalena",
        "São Sebastião do Alto",
        "Trajano de Moraes",
    ],
    "Baixadas Litorâneas": [
        "Cabo Frio",
        "Araruama",
        "Armação dos Búzios",
        "Arraial do Cabo",
        "Casimiro de Abreu",
        "Iguaba Grande",
        "Rio das Ostras",
        "São Pedro da Aldeia",
        "Saquarema",
        "Silva Jardim",
    ],
    "Lagos": [
        "Saquarema",
        "Araruama",
        "Cabo Frio",
        "Búzios",
    ],
    "Médio Paraíba": [
        "Volta Redonda",
        "Barra Mansa",
        "Resende",
        "Itatiaia",
        "Porto Real",
        "Quatis",
        "Rio Claro",
        "Barra do Piraí",
        "Valença",
        "Paraíba do Sul",
        "Três Rios",
        "Vassouras",
        "Engenheiro Paulo de Frontin",
        "Miguel Pereira",
        "Mendes",
        "Piraí",
    ],
    "Norte": [
        "Campos dos Goytacazes",
        "São Fidélis",
        "Cardoso Moreira",
        "São Francisco de Itabapoana",
        "São João da Barra",
        "Quissamã",
    ],
    "Noroeste": [
        "Itaperuna",
        "Miracema",
        "Natividade",
        "Bom Jesus do Itabapoana",
        "Porciúncula",
        "Varre-Sai",
        "Italva",
        "Laje do Muriaé",
        "São José de Ubá",
    ],
    "Costa Verde": [
        "Angra dos Reis",
        "Mangaratiba",
        "Paraty",
    ],
}

# Inverte mapa: município → macrorregião
_MUN_TO_REGIAO: dict[str, str] = {}
for _reg, _muns in RJ_MACRORREGIOES.items():
    for _m in _muns:
        _MUN_TO_REGIAO[_m] = _reg


def municipio_to_regiao(nm_municipio: str) -> str:
    """Retorna a macrorregião de um município do RJ. Fallback: Metropolitana."""
    return _MUN_TO_REGIAO.get(nm_municipio, "Metropolitana")


def prepare_governador_features(
    df: pd.DataFrame,
    train_stats: dict | None = None,
) -> tuple[dict, dict]:
    """Prepare features for the governador (majoritarian) model.

    Expected columns in df:
      Obrigatórias:
        candidato, sg_uf (ou macrorregiao), pct_votos_historico,
        media_intencao_voto, y (pct_votos outcome)
      Opcionais (zeradas se ausentes):
        taxa_rejeicao, saldo_intencao_rejeicao, apoios_prefeitos_ponderados,
        is_new_candidate, poll_missing,
        populacao_total, renda_per_capita, taxa_alfabetizacao

    Returns:
        (features, stats) — features dict para build_governador_model()
    """
    demo_cols = ["log_populacao", "log_renda", "taxa_alfabetizacao"]
    extra_cols = ["taxa_rejeicao", "saldo_intencao_rejeicao", "apoios_prefeitos_ponderados"]

    df = df.copy()
    df["log_populacao"] = np.log1p(
        df.get("populacao_total", pd.Series(0, index=df.index)).fillna(0)
    )
    df["log_renda"] = np.log1p(df.get("renda_per_capita", pd.Series(0, index=df.index)).fillna(0))
    df["taxa_alfabetizacao"] = (
        df["taxa_alfabetizacao"].fillna(85.0)
        if "taxa_alfabetizacao" in df
        else pd.Series(85.0, index=df.index)
    )

    for col in extra_cols:
        if col not in df:
            df[col] = 0.0
        else:
            df[col] = df[col].fillna(0.0)

    all_feat_cols = demo_cols + extra_cols

    if train_stats is None:
        train_mean = {c: float(df[c].mean()) for c in all_feat_cols}
        train_std = {c: float(df[c].std()) for c in all_feat_cols}
    else:
        train_mean = train_stats["feat_mean"]
        train_std = train_stats["feat_std"]

    X = np.column_stack(
        [(df[c].values - train_mean[c]) / (train_std[c] + 1e-6) for c in all_feat_cols]
    )

    # Macrorregião encoding
    if "macrorregiao" in df.columns:
        regiao_series = df["macrorregiao"].fillna("Metropolitana")
    else:
        regiao_series = pd.Series("Metropolitana", index=df.index)

    regiao_cat = pd.Categorical(regiao_series, categories=list(RJ_MACRORREGIOES.keys()))
    regiao_idx = regiao_cat.codes
    regiao_labels = list(RJ_MACRORREGIOES.keys())

    # Candidate grouping
    if train_stats is None:
        top_cands = df["candidato"].value_counts().head(10).index.tolist()
    else:
        top_cands = train_stats["top_candidates"]

    df["cand_grouped"] = df["candidato"].where(df["candidato"].isin(top_cands), "OTHER")
    cand_cat = pd.Categorical(df["cand_grouped"])
    cand_idx = cand_cat.codes
    cand_labels = list(cand_cat.categories)

    is_new = (
        df["is_new_candidate"].fillna(0).values.astype(int)
        if "is_new_candidate" in df
        else np.zeros(len(df), dtype=int)
    )
    poll_missing = (
        df["poll_missing"].fillna(0).values.astype(int)
        if "poll_missing" in df
        else np.zeros(len(df), dtype=int)
    )

    features = {
        "X": X,
        "hist": df["pct_votos_historico"].fillna(0.0).values.astype(float),
        "poll": df["media_intencao_voto"].fillna(0.0).values.astype(float),
        "rejeicao": df["taxa_rejeicao"].values.astype(float),
        "saldo": df["saldo_intencao_rejeicao"].values.astype(float),
        "apoio": df["apoios_prefeitos_ponderados"].values.astype(float),
        "is_new": is_new,
        "poll_missing": poll_missing,
        "regiao_idx": regiao_idx,
        "cand_idx": cand_idx,
        "regiao_labels": regiao_labels,
        "cand_labels": cand_labels,
        "n_regiao": len(regiao_labels),
        "n_cand": len(cand_labels),
        "n_feat": X.shape[1],
        "y": df["y"].values.astype(float),
    }

    stats = {
        "feat_cols": all_feat_cols,
        "feat_mean": train_mean,
        "feat_std": train_std,
        "regiao_labels": regiao_labels,
        "cand_labels": cand_labels,
        "top_candidates": top_cands,
    }

    return features, stats


def build_governador_model(features: dict):
    """Build hierarchical PyMC model for governador (majoritarian) election.

    Structure:
      Level 1: Macrorregião intercept (8 regiões RJ, non-centered partial pooling)
      Level 2: Candidate intercept (non-centered; mu_cand=0 anchored)
      Level 3: Anchoring signals w_hist, w_poll (TruncatedNormal, lower=0)
      Level 4: Rejection penalty w_rej, support bonus w_apoio, saldo w_saldo
      Level 5: Regional-varying slopes beta[reg, feat] (non-centered)
      Likelihood: Beta(mu*kappa, (1-mu)*kappa)

    Calibration for majoritarian race (4-8 candidates, pct_votos 15-50%):
      kappa ~ HalfNormal(20): prior mean ≈ 16 → var ≈ 0.012 at mu=0.35
      kappa larger than electoral model (50) — less noise in 2-round races
    """
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC required: pip install pymc arviz")

    n_regiao = features["n_regiao"]
    n_cand = features["n_cand"]
    n_feat = features["n_feat"]

    with pm.Model() as model:
        X_data = pm.MutableData("X_data", features["X"])
        hist_data = pm.MutableData("hist_data", features["hist"])
        poll_data = pm.MutableData("poll_data", features["poll"])
        rej_data = pm.MutableData("rej_data", features["rejeicao"])
        saldo_data = pm.MutableData("saldo_data", features["saldo"])
        apoio_data = pm.MutableData("apoio_data", features["apoio"])
        is_new_data = pm.MutableData("is_new_data", features["is_new"])
        poll_miss = pm.MutableData("poll_missing_data", features["poll_missing"])
        reg_data = pm.MutableData("reg_idx_data", features["regiao_idx"])
        cand_data = pm.MutableData("cand_idx_data", features["cand_idx"])

        # ── Level 1: Macrorregião intercept (non-centered) ───────────────────
        mu_reg = pm.Normal("mu_reg", 0.0, 1.0)
        sd_reg = pm.HalfNormal("sd_reg", 0.5)
        a_raw = pm.Normal("a_raw", 0.0, 1.0, shape=n_regiao)
        a_reg = pm.Deterministic("a_reg", mu_reg + sd_reg * a_raw)

        # ── Level 2: Candidate intercept (non-centered) ──────────────────────
        sd_cand = pm.HalfNormal("sd_cand", 0.8)
        g_raw = pm.Normal("g_raw", 0.0, 1.0, shape=n_cand)
        gamma = pm.Deterministic("gamma", sd_cand * g_raw)

        # ── Level 3: Anchoring — historical + poll ───────────────────────────
        w_hist = pm.TruncatedNormal("w_hist", mu=2.0, sigma=0.5, lower=0.0)
        w_poll = pm.TruncatedNormal("w_poll", mu=2.5, sigma=0.5, lower=0.0)

        # ── Level 4: Rejection + support signals ─────────────────────────────
        # w_rej penalises — rejeição alta teto o candidato (Witzel 2022 efeito)
        # w_apoio bonifica candidatos com apoio de prefeitos de grande eleitorado
        # w_saldo captura o gap intenção−rejeição (candidatos viáveis têm saldo+)
        w_rej = pm.TruncatedNormal("w_rej", mu=1.0, sigma=0.5, lower=0.0)
        w_apoio = pm.TruncatedNormal("w_apoio", mu=0.5, sigma=0.3, lower=0.0)
        w_saldo = pm.TruncatedNormal("w_saldo", mu=0.8, sigma=0.4, lower=0.0)

        # ── Level 5: Region-varying slopes (hierarchical, non-centered) ──────
        mu_beta = pm.Normal("mu_beta", 0.0, 0.3, shape=n_feat)
        sd_beta = pm.HalfNormal("sd_beta", 0.3, shape=n_feat)
        beta_raw = pm.Normal("beta_raw", 0.0, 1.0, shape=(n_regiao, n_feat))
        beta = pm.Deterministic("beta", mu_beta + sd_beta * beta_raw)

        # ── Linear predictor ─────────────────────────────────────────────────
        eta = (
            a_reg[reg_data]
            + gamma[cand_data]
            + w_hist * hist_data * (1 - is_new_data)
            + w_poll * poll_data * (1 - poll_miss)
            - w_rej * rej_data
            + w_apoio * apoio_data
            + w_saldo * saldo_data
            + (X_data * beta[reg_data]).sum(axis=1)
        )
        mu = pm.Deterministic("mu", pm.math.sigmoid(eta))

        # ── Beta likelihood (tighter for majoritarian — less noise) ──────────
        kappa = pm.HalfNormal("kappa", sigma=20.0)
        pm.Beta("y_obs", alpha=mu * kappa, beta=(1 - mu) * kappa, observed=features["y"])

    return model


def sample_governador_posterior(
    model,
    draws: int = 1000,
    tune: int = 2000,
    chains: int = 4,
    target_accept: float = 0.95,
    random_seed: int = 42,
):
    """Sample from governador model posterior using NUTS."""
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


def predict_governador(
    model,
    idata,
    features_new: dict,
    features_train_stats: dict,
) -> pd.DataFrame:
    """Generate posterior predictive for new data.

    Returns DataFrame with columns:
      candidato, macrorregiao, prob_vitoria, pct_votos_mean, pct_votos_p10, pct_votos_p90
    """
    try:
        import pymc as pm
    except ImportError:
        raise ImportError("PyMC + ArviZ required")

    with model:
        for key, val in [
            ("X_data", features_new["X"]),
            ("hist_data", features_new["hist"]),
            ("poll_data", features_new["poll"]),
            ("rej_data", features_new["rejeicao"]),
            ("saldo_data", features_new["saldo"]),
            ("apoio_data", features_new["apoio"]),
            ("is_new_data", features_new["is_new"]),
            ("poll_missing_data", features_new["poll_missing"]),
            ("reg_idx_data", features_new["regiao_idx"]),
            ("cand_idx_data", features_new["cand_idx"]),
        ]:
            pm.set_data({key: val})

        ppc = pm.sample_posterior_predictive(idata, var_names=["mu", "y_obs"], progressbar=False)

    mu_samples = ppc.posterior_predictive["mu"].values  # (chain, draw, n_obs)
    mu_flat = mu_samples.reshape(-1, mu_samples.shape[-1])  # (samples, n_obs)

    rows = []
    for i in range(mu_flat.shape[1]):
        rows.append(
            {
                "pct_votos_mean": float(mu_flat[:, i].mean()),
                "pct_votos_p10": float(np.percentile(mu_flat[:, i], 10)),
                "pct_votos_p90": float(np.percentile(mu_flat[:, i], 90)),
            }
        )

    df = pd.DataFrame(rows)
    df["prob_lider"] = (mu_flat == mu_flat.max(axis=1, keepdims=True)).mean(axis=0)
    return df
