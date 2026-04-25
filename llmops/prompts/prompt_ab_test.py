from __future__ import annotations

import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PromptAResult:
    prompt_id: str
    score_sum: float = 0.0
    n: int = 0

    @property
    def mean(self) -> float:
        return self.score_sum / self.n if self.n else 0.0


@dataclass
class ABTestOutcome:
    winner: str | None
    p_value: float
    power: float
    sample_size: int
    current_mean: float
    candidate_mean: float


class PromptABTest:
    """50/50 split between current and candidate prompts.

    Promotes the candidate once statistical power > target (default 0.80).
    """

    def __init__(
        self,
        current_prompt_id: str,
        candidate_prompt_id: str,
        target_power: float = 0.80,
        alpha: float = 0.05,
        min_samples: int = 100,
    ):
        self.current = PromptAResult(current_prompt_id)
        self.candidate = PromptAResult(candidate_prompt_id)
        self.target_power = target_power
        self.alpha = alpha
        self.min_samples = min_samples
        self._current_scores: list[float] = []
        self._candidate_scores: list[float] = []

    def choose_prompt(self) -> str:
        return self.current.prompt_id if random.random() < 0.5 else self.candidate.prompt_id

    def record_score(self, prompt_id: str, score: float) -> None:
        if prompt_id == self.current.prompt_id:
            self.current.score_sum += score
            self.current.n += 1
            self._current_scores.append(score)
        elif prompt_id == self.candidate.prompt_id:
            self.candidate.score_sum += score
            self.candidate.n += 1
            self._candidate_scores.append(score)
        else:
            logger.warning("unknown_prompt_id=%s", prompt_id)

    def analyze(self) -> ABTestOutcome:
        n = min(self.current.n, self.candidate.n)
        if n < self.min_samples:
            return ABTestOutcome(
                winner=None,
                p_value=1.0,
                power=0.0,
                sample_size=n,
                current_mean=self.current.mean,
                candidate_mean=self.candidate.mean,
            )
        try:
            from scipy import stats

            t, p_value = stats.ttest_ind(
                self._candidate_scores,
                self._current_scores,
                equal_var=False,
            )
            effect_size = abs(self.candidate.mean - self.current.mean) / max(
                stats.tstd(self._current_scores + self._candidate_scores), 1e-6
            )
            power = min(1.0, 0.8 * effect_size * (n**0.5) / 10)
        except ImportError:
            p_value, power = 1.0, 0.0
        winner = None
        if p_value < self.alpha and power >= self.target_power:
            winner = (
                self.candidate.prompt_id
                if self.candidate.mean > self.current.mean
                else self.current.prompt_id
            )
        return ABTestOutcome(
            winner=winner,
            p_value=float(p_value),
            power=float(power),
            sample_size=n,
            current_mean=self.current.mean,
            candidate_mean=self.candidate.mean,
        )
