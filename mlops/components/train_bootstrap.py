"""Bootstrap logistic regression with IC 95% — MVP Bayesian approximation."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.bootstrap")


@dataclass
class Prediction:
    point_estimate: float
    ci_lower: float
    ci_upper: float
    n_bootstrap: int
    alpha: float
    method: str = "bootstrap_logistic"
    warnings: list[str] = None

    def to_str(self) -> str:
        pct = self.point_estimate * 100
        lo = self.ci_lower * 100
        hi = self.ci_upper * 100
        return f"P = {pct:.1f}% [IC 95%: {lo:.1f}%–{hi:.1f}%]"

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def predict_with_ic(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_pred: np.ndarray,
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
) -> Prediction:
    """Bootstrap logistic regression for IC 95% prediction."""
    import statsmodels.api as sm

    warnings = []
    n = len(X_train)

    if n < 100:
        warnings.append(f"Amostra pequena ({n} observações) — IC mais amplo que o usual.")
        n_bootstrap = min(n_bootstrap * 2, 5000)

    X_train_c = sm.add_constant(X_train, has_constant="add")
    X_pred_c = sm.add_constant(X_pred.reshape(1, -1) if X_pred.ndim == 1 else X_pred, has_constant="add")

    predictions = []
    failed = 0

    for _ in range(n_bootstrap):
        idx = np.random.choice(n, n, replace=True)
        try:
            model = sm.Logit(y_train[idx], X_train_c[idx])
            result = model.fit(disp=False, maxiter=200, warn_convergence=False)
            pred = result.predict(X_pred_c)
            predictions.append(float(pred[0]) if len(pred) > 0 else np.nan)
        except Exception:
            failed += 1
            continue

    predictions = [p for p in predictions if not np.isnan(p)]

    if len(predictions) < n_bootstrap * 0.5:
        warnings.append(f"Alto índice de falhas no bootstrap ({failed}/{n_bootstrap}). IC pode ser impreciso.")

    if not predictions:
        return Prediction(
            point_estimate=0.5,
            ci_lower=0.0,
            ci_upper=1.0,
            n_bootstrap=0,
            alpha=alpha,
            warnings=["Bootstrap falhou completamente. Retornando estimativa neutra."],
        )

    preds = np.array(predictions)
    return Prediction(
        point_estimate=float(np.mean(preds)),
        ci_lower=float(np.percentile(preds, alpha / 2 * 100)),
        ci_upper=float(np.percentile(preds, (1 - alpha / 2) * 100)),
        n_bootstrap=len(preds),
        alpha=alpha,
        warnings=warnings,
    )


def prepare_features_from_gold(
    df: pd.DataFrame,
    candidate_col: str,
    target_candidate: str,
    feature_cols: list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Prepare X, y arrays from Gold fact_municipio_eleicao."""
    if feature_cols is None:
        feature_cols = [
            c for c in df.columns
            if any(ind in c for ind in ["idhm", "renda", "estudo", "pct_rural", "populacao", "pib"])
            and c in df.columns
        ]

    if candidate_col in df.columns:
        y = (df[candidate_col] == target_candidate).astype(int).values
    else:
        target_col = f"pct_votos_{target_candidate.lower().replace(' ', '_')}_2022"
        if target_col in df.columns:
            y = (df[target_col] > 0.5).astype(int).values
        else:
            y = np.zeros(len(df), dtype=int)

    available_cols = [c for c in feature_cols if c in df.columns]
    X = df[available_cols].fillna(0).values
    return X, y, available_cols
