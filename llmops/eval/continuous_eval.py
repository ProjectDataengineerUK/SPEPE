from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Callable

from llmops.eval.metrics import (
    disclaimer_present_rate,
    factuality,
    relevance,
)

logger = logging.getLogger(__name__)


@dataclass
class EvalScore:
    score: float
    disclaimer_rate: float
    relevance: float
    factuality: float
    n_samples: int


class ContinuousEval:
    """Reservoir sampling 5% of production outputs for continuous evaluation."""

    def __init__(
        self,
        sample_rate: float = 0.05,
        alert_threshold: float = 0.85,
        max_reservoir_size: int = 500,
    ):
        self.sample_rate = sample_rate
        self.alert_threshold = alert_threshold
        self.max_reservoir_size = max_reservoir_size
        self._reservoir: list[dict[str, Any]] = []
        self._seen = 0

    def maybe_sample(self, output: str, context: dict[str, Any]) -> bool:
        self._seen += 1
        if len(self._reservoir) < self.max_reservoir_size:
            if random.random() < self.sample_rate:
                self._reservoir.append({"output": output, "context": context})
                return True
            return False
        idx = random.randint(0, self._seen - 1)
        if idx < self.max_reservoir_size:
            self._reservoir[idx] = {"output": output, "context": context}
            return True
        return False

    def evaluate(
        self,
        alert_fn: Callable[[str, EvalScore], None] | None = None,
    ) -> EvalScore:
        outputs = [item["output"] for item in self._reservoir]
        expected_keywords: list[str] = []
        ground_truths: list[str] = []
        for item in self._reservoir:
            expected_keywords.extend(item["context"].get("expected_keywords", []))
            ground_truths.extend(item["context"].get("ground_truths", []))

        dp_rate = disclaimer_present_rate(outputs) if outputs else 0.0
        rel_score = (
            sum(relevance(o, expected_keywords) for o in outputs) / len(outputs) if outputs else 0.0
        )
        fact_score = (
            sum(factuality(o, ground_truths) for o in outputs) / len(outputs) if outputs else 0.0
        )
        score = (dp_rate + rel_score + fact_score) / 3.0
        result = EvalScore(
            score=score,
            disclaimer_rate=dp_rate,
            relevance=rel_score,
            factuality=fact_score,
            n_samples=len(outputs),
        )
        if score < self.alert_threshold and alert_fn is not None:
            alert_fn(
                f"continuous_eval_below_threshold score={score:.3f} "
                f"threshold={self.alert_threshold}",
                result,
            )
        return result

    def reset(self) -> None:
        self._reservoir.clear()
        self._seen = 0
