"""Poll aggregation with house effect adjustment per institute."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.polls")

HOUSE_EFFECTS: dict[str, float] = {
    "datafolha": 0.0,
    "ibope": -1.2,
    "sensus": 1.5,
    "vox_populi": 0.8,
    "atlas": -0.5,
    "quaest": 0.3,
    "genial_quaest": 0.3,
    "paraná_pesquisas": -0.7,
    "ipespe": 0.2,
}


def aggregate_polls(
    df_polls: pd.DataFrame,
    candidate: str,
    candidate_col: str = "candidato",
    intencao_col: str = "intencao_pct",
    instituto_col: str = "instituto",
    date_col: str = "data_pesquisa",
    weight_by_recency: bool = True,
    alpha: float = 0.05,
) -> dict:
    """Aggregate polls with house effect adjustment and IC 95%."""
    if df_polls.empty:
        return {"status": "no_data", "message": "Nenhuma pesquisa disponível."}

    if candidate_col in df_polls.columns:
        df = df_polls[df_polls[candidate_col].str.contains(candidate, case=False, na=False)].copy()
    else:
        df = df_polls.copy()

    if df.empty:
        return {"status": "no_data", "message": f"Nenhuma pesquisa para '{candidate}'."}

    if intencao_col not in df.columns:
        return {"status": "error", "message": f"Coluna '{intencao_col}' não encontrada."}

    df["intencao_pct_num"] = pd.to_numeric(df[intencao_col], errors="coerce")
    df = df.dropna(subset=["intencao_pct_num"])

    if df.empty:
        return {"status": "no_data", "message": f"Nenhum valor numérico válido para '{candidate}'."}

    if instituto_col in df.columns:
        df["house_effect"] = df[instituto_col].str.lower().map(
            lambda x: HOUSE_EFFECTS.get(x.strip(), 0.0) if pd.notna(x) else 0.0
        )
    else:
        df["house_effect"] = 0.0

    df["intencao_ajustada"] = df["intencao_pct_num"] - df["house_effect"]

    if weight_by_recency and date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        max_date = df[date_col].max()
        days_ago = (max_date - df[date_col]).dt.days.fillna(30)
        df["weight"] = np.exp(-days_ago / 30)
    else:
        df["weight"] = 1.0

    total_weight = df["weight"].sum()
    weighted_mean = float((df["intencao_ajustada"] * df["weight"]).sum() / total_weight)

    n = len(df)
    std = float(df["intencao_ajustada"].std())
    se = std / np.sqrt(n) if n > 1 else std
    z = 1.96
    ci_lo = weighted_mean - z * se
    ci_hi = weighted_mean + z * se

    return {
        "status": "ok",
        "candidate": candidate,
        "n_polls": n,
        "weighted_mean_pct": weighted_mean,
        "ci_lower_pct": ci_lo,
        "ci_upper_pct": ci_hi,
        "alpha": alpha,
        "institutes": df[instituto_col].unique().tolist() if instituto_col in df.columns else [],
        "summary": f"P({candidate}) = {weighted_mean:.1f}% [IC 95%: {ci_lo:.1f}%–{ci_hi:.1f}%] (n={n} pesquisas)",
    }
