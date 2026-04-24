from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PRICE_USD_PER_1M_TOKENS = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.0},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


@dataclass
class CostEvent:
    agent: str
    session_id: str
    user_id: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: float


class CostAttributor:
    """Attributes LLM cost to agent/session/user and emits structured logs."""

    def __init__(self):
        self._totals: dict[tuple[str, str, str], float] = defaultdict(float)

    def record(
        self,
        agent: str,
        session_id: str,
        user_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> CostEvent:
        prices = PRICE_USD_PER_1M_TOKENS.get(model, {"input": 0.0, "output": 0.0})
        cost = (
            input_tokens * prices["input"] + output_tokens * prices["output"]
        ) / 1_000_000
        event = CostEvent(
            agent=agent,
            session_id=session_id,
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=round(cost, 6),
        )
        self._totals[(agent, session_id, user_id)] += event.usd
        self._emit(event)
        return event

    def session_total(self, session_id: str) -> float:
        return sum(
            v for (_a, s, _u), v in self._totals.items() if s == session_id
        )

    def user_total(self, user_id: str) -> float:
        return sum(
            v for (_a, _s, u), v in self._totals.items() if u == user_id
        )

    def agent_total(self, agent: str) -> float:
        return sum(
            v for (a, _s, _u), v in self._totals.items() if a == agent
        )

    def breakdown(self) -> list[dict[str, Any]]:
        return [
            {
                "agent": a,
                "session_id": s,
                "user_id": u,
                "usd": round(v, 6),
            }
            for (a, s, u), v in self._totals.items()
        ]

    @staticmethod
    def _emit(event: CostEvent) -> None:
        logger.info(
            "llm_cost %s",
            json.dumps(
                {
                    "agent": event.agent,
                    "session_id": event.session_id,
                    "user_id": event.user_id,
                    "model": event.model,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "usd": event.usd,
                }
            ),
        )
