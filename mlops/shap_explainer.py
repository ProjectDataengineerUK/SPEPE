"""SHAP explainability for electoral prediction models."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.shap")

SHAP_OUTPUT_DIR = Path("output/shap")


def get_shap_explanation(
    model,
    X: pd.DataFrame,
    feature_names: list[str],
    top_n: int = 10,
) -> list[dict]:
    """Compute SHAP top-N feature importances."""
    try:
        import shap
    except ImportError:
        raise ImportError("shap não instalado. Execute: pip install shap")

    try:
        explainer = shap.LinearExplainer(model, X)
    except Exception:
        try:
            explainer = shap.TreeExplainer(model)
        except Exception:
            explainer = shap.KernelExplainer(model.predict_proba, shap.sample(X, 50))

    shap_values = explainer(X)
    values = shap_values.values if hasattr(shap_values, "values") else shap_values

    if values.ndim == 3:
        values = values[:, :, 1]

    mean_abs_shap = np.abs(values).mean(axis=0)
    mean_shap = values.mean(axis=0)
    top_idx = np.argsort(mean_abs_shap)[::-1][:top_n]

    result = [
        {
            "rank": i + 1,
            "feature": feature_names[idx],
            "importance": float(mean_abs_shap[idx]),
            "direction": "positivo" if mean_shap[idx] > 0 else "negativo",
            "mean_shap": float(mean_shap[idx]),
        }
        for i, idx in enumerate(top_idx)
    ]

    _save_shap_summary(result)
    return result


def _save_shap_summary(result: list[dict], filename: str = "shap_values_latest.json") -> None:
    SHAP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHAP_OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"SHAP summary salvo: {path}")


def load_shap_summary(path: str = "output/shap/shap_values_latest.json") -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def shap_to_markdown(shap_values: list[dict]) -> str:
    lines = [
        "| Rank | Variável | Importância | Direção |",
        "|------|----------|-------------|---------|",
    ]
    for item in shap_values:
        arrow = "↑" if item["direction"] == "positivo" else "↓"
        lines.append(
            f"| {item['rank']} | `{item['feature']}` | {item['importance']:.4f} | {arrow} {item['direction']} |"
        )
    return "\n".join(lines)
