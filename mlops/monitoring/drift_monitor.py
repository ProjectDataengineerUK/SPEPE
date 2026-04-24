"""Computes Jensen-Shannon divergence per feature; publishes drift events to Pub/Sub."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "drift_config.yaml"


@dataclass
class DriftResult:
    feature: str
    js_score: float
    threshold: float
    drifted: bool
    reference_n: int
    current_n: int


def _load_config() -> dict[str, Any]:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _js_divergence(p: np.ndarray, q: np.ndarray, n_bins: int = 20) -> float:
    """Jensen-Shannon divergence via histogram binning (0.0–1.0 range)."""
    combined = np.concatenate([p, q])
    bins = np.linspace(combined.min(), combined.max() + 1e-10, n_bins + 1)
    p_hist, _ = np.histogram(p, bins=bins, density=True)
    q_hist, _ = np.histogram(q, bins=bins, density=True)

    p_hist = p_hist + 1e-10
    q_hist = q_hist + 1e-10
    p_hist /= p_hist.sum()
    q_hist /= q_hist.sum()

    m = 0.5 * (p_hist + q_hist)
    js = 0.5 * np.sum(p_hist * np.log(p_hist / m)) + 0.5 * np.sum(q_hist * np.log(q_hist / m))
    return float(np.clip(js, 0.0, 1.0))


def _categorical_divergence(p: pd.Series, q: pd.Series) -> float:
    cats = set(p.unique()) | set(q.unique())
    p_freq = p.value_counts(normalize=True)
    q_freq = q.value_counts(normalize=True)

    p_arr = np.array([p_freq.get(c, 1e-10) for c in cats])
    q_arr = np.array([q_freq.get(c, 1e-10) for c in cats])
    p_arr /= p_arr.sum()
    q_arr /= q_arr.sum()

    m = 0.5 * (p_arr + q_arr)
    js = 0.5 * np.sum(p_arr * np.log(p_arr / m)) + 0.5 * np.sum(q_arr * np.log(q_arr / m))
    return float(np.clip(js, 0.0, 1.0))


def detect_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    publish: bool = True,
    project_id: str | None = None,
) -> list[DriftResult]:
    config = _load_config()
    js_threshold = config.get("js_divergence_threshold", 0.10)
    cat_threshold = config.get("categorical_threshold", 0.05)
    check_features: list[dict] = config.get("check_features", [])

    results: list[DriftResult] = []
    drifted_features: list[str] = []

    for feature_cfg in check_features:
        if isinstance(feature_cfg, str):
            feat_name = feature_cfg
            is_categorical = False
            threshold = js_threshold
        else:
            feat_name = feature_cfg.get("name", feature_cfg)
            is_categorical = feature_cfg.get("type") == "categorical"
            threshold = cat_threshold if is_categorical else js_threshold

        if feat_name not in reference_df.columns or feat_name not in current_df.columns:
            logger.warning("Feature %s not found in dataframes — skipping", feat_name)
            continue

        ref = reference_df[feat_name].dropna()
        cur = current_df[feat_name].dropna()

        if is_categorical:
            score = _categorical_divergence(ref, cur)
        else:
            score = _js_divergence(ref.values, cur.values)

        drifted = score > threshold
        result = DriftResult(
            feature=feat_name,
            js_score=score,
            threshold=threshold,
            drifted=drifted,
            reference_n=len(ref),
            current_n=len(cur),
        )
        results.append(result)

        if drifted:
            drifted_features.append(feat_name)
            logger.warning("DRIFT DETECTED: %s  JS=%.4f > threshold=%.4f", feat_name, score, threshold)

    if drifted_features and publish:
        from mlops.monitoring.pubsub_publisher import publish_drift_event
        for feat in drifted_features:
            r = next(r for r in results if r.feature == feat)
            publish_drift_event(
                feature=feat,
                js_score=r.js_score,
                threshold=r.threshold,
                project_id=project_id,
            )

    return results


def drift_summary(results: list[DriftResult]) -> dict[str, Any]:
    drifted = [r for r in results if r.drifted]
    return {
        "total_features": len(results),
        "drifted_features": len(drifted),
        "drifted_names": [r.feature for r in drifted],
        "max_score": max((r.js_score for r in results), default=0.0),
        "drift_detected": len(drifted) > 0,
    }
