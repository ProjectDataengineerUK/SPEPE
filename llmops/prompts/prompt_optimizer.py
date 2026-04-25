from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class PromptCandidate:
    prompt_id: str
    text: str
    score: float = 0.0


class PromptOptimizer:
    """Gradient-free prompt optimization via instruction-level variations.

    Generates variations of a base prompt, evaluates each via `eval_fn`,
    and returns the highest-scoring candidate.
    """

    VARIATION_TEMPLATES = [
        "{base}\n\nSeja objetivo e direto.",
        "{base}\n\nInclua intervalos de confianca sempre que possivel.",
        "{base}\n\nExplique cada premissa antes da conclusao.",
        "{base}\n\nCite fontes (TSE, IBGE) ao final.",
        "{base}\n\nResponda em linguagem acessivel a leigos.",
    ]

    def __init__(self, eval_fn: Callable[[str], float]):
        self.eval_fn = eval_fn

    def generate_variations(self, base: str) -> list[PromptCandidate]:
        candidates = [PromptCandidate(prompt_id="base", text=base)]
        for i, tpl in enumerate(self.VARIATION_TEMPLATES):
            candidates.append(
                PromptCandidate(prompt_id=f"var_{i:02d}", text=tpl.format(base=base))
            )
        return candidates

    def optimize(self, base: str) -> PromptCandidate:
        candidates = self.generate_variations(base)
        for c in candidates:
            try:
                c.score = float(self.eval_fn(c.text))
            except Exception as exc:
                logger.warning("eval_failed for=%s err=%s", c.prompt_id, exc)
                c.score = 0.0
        best = max(candidates, key=lambda x: x.score)
        logger.info("best_prompt id=%s score=%.3f", best.prompt_id, best.score)
        return best
