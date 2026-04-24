from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class AnomalyResult:
    is_anomaly: bool
    z_score: float
    iqr_flag: bool
    correlations: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class AnomalyDetector:
    """Z-score + IQR per metric; correlates signals from multiple watchers."""

    def __init__(self, z_threshold: float = 3.0, iqr_multiplier: float = 1.5):
        self.z_threshold = z_threshold
        self.iqr_multiplier = iqr_multiplier

    def detect(
        self,
        current_value: float,
        history: list[float],
        correlated_observations: dict[str, dict] | None = None,
    ) -> AnomalyResult:
        if not history:
            return AnomalyResult(False, 0.0, False, [], {"reason": "no_history"})

        arr = np.asarray(history, dtype=float)
        mu = float(arr.mean())
        sigma = float(arr.std()) or 1e-6
        z = (current_value - mu) / sigma

        q1 = float(np.quantile(arr, 0.25))
        q3 = float(np.quantile(arr, 0.75))
        iqr = q3 - q1
        iqr_low = q1 - self.iqr_multiplier * iqr
        iqr_high = q3 + self.iqr_multiplier * iqr
        iqr_flag = current_value < iqr_low or current_value > iqr_high

        correlations = self._correlate(correlated_observations or {})

        return AnomalyResult(
            is_anomaly=abs(z) > self.z_threshold or iqr_flag,
            z_score=float(z),
            iqr_flag=iqr_flag,
            correlations=correlations,
            metrics={
                "mu": mu,
                "sigma": sigma,
                "q1": q1,
                "q3": q3,
                "iqr_low": iqr_low,
                "iqr_high": iqr_high,
                "current": current_value,
            },
        )

    def _correlate(self, obs: dict[str, dict]) -> list[str]:
        firing = [name for name, o in obs.items() if o.get("detected")]
        return firing
