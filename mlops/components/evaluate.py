"""Model evaluation: backtesting 2018→2022, error metrics."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("spepe.mlops.evaluate")

EVAL_OUTPUT_DIR = Path("output/model_eval")


def backtest_2018_2022(
    model,
    X_2018: np.ndarray,
    y_2022: np.ndarray,
    feature_names: list[str],
) -> dict:
    """Train on 2018 data, predict 2022, compute observed error."""
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

    y_pred_proba = model.predict_proba(X_2018)[:, 1] if hasattr(model, "predict_proba") else model.predict(X_2018)
    y_pred = (y_pred_proba >= 0.5).astype(int)

    metrics = {
        "accuracy": float(accuracy_score(y_2022, y_pred)),
        "brier_score": float(brier_score_loss(y_2022, y_pred_proba)),
        "log_loss": float(log_loss(y_2022, y_pred_proba)),
        "mean_absolute_error": float(np.abs(y_pred_proba - y_2022).mean()),
        "n_samples": len(y_2022),
    }

    _save_eval(metrics, "backtest_2018_2022.json")
    logger.info(f"Backtest 2018→2022: accuracy={metrics['accuracy']:.3f} MAE={metrics['mean_absolute_error']:.3f}")
    return metrics


def compute_calibration(y_true: np.ndarray, y_pred_proba: np.ndarray, n_bins: int = 10) -> dict:
    """Compute calibration metrics (reliability diagram data)."""
    bins = np.linspace(0, 1, n_bins + 1)
    bin_means = []
    bin_fracs = []

    for i in range(n_bins):
        mask = (y_pred_proba >= bins[i]) & (y_pred_proba < bins[i + 1])
        if mask.sum() > 0:
            bin_means.append(float(y_pred_proba[mask].mean()))
            bin_fracs.append(float(y_true[mask].mean()))

    ece = float(np.mean([abs(m - f) for m, f in zip(bin_means, bin_fracs)]))
    return {"ece": ece, "bin_means": bin_means, "bin_fracs": bin_fracs}


def _save_eval(metrics: dict, filename: str) -> None:
    EVAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
