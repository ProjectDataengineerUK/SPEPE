from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DriftSignal:
    metric: str
    z_score: float
    mean: float
    std: float
    current: float


class OutputDriftMonitor:
    """Monitors output distribution metrics: disclaimer_rate, confidence, tokens.

    Alerts when any metric drifts beyond +/- `z_threshold` standard deviations.
    """

    def __init__(self, window: int = 500, z_threshold: float = 2.0):
        self.window = window
        self.z_threshold = z_threshold
        self._series: dict[str, deque[float]] = {}

    def record(self, metric: str, value: float) -> DriftSignal | None:
        series = self._series.setdefault(metric, deque(maxlen=self.window))
        if len(series) < self.window // 2:
            series.append(value)
            return None
        arr = np.asarray(series, dtype=float)
        mean = float(arr.mean())
        std = float(arr.std()) or 1e-6
        z = (value - mean) / std
        series.append(value)
        if abs(z) > self.z_threshold:
            logger.warning("output_drift metric=%s z=%.2f current=%.3f", metric, z, value)
            return DriftSignal(metric=metric, z_score=z, mean=mean, std=std, current=value)
        return None

    def snapshot(self) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for metric, series in self._series.items():
            if not series:
                continue
            arr = np.asarray(series, dtype=float)
            result[metric] = {
                "mean": float(arr.mean()),
                "std": float(arr.std()),
                "min": float(arr.min()),
                "max": float(arr.max()),
                "n": len(arr),
            }
        return result
