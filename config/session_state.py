"""Per-session state — replaces global variables in cost_guard_hook."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from config.exceptions import BudgetExceededError

Role = Literal["user", "supervisor", "agent"]

BUDGET_USD = 2.00
BUDGET_WARN_USD = 1.50


@dataclass
class Turn:
    role: Role
    content: str
    agent_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SessionState:
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    total_cost_usd: float = 0.0

    def add_cost(self, usd: float) -> None:
        self.total_cost_usd += usd
        if self.total_cost_usd >= BUDGET_USD:
            raise BudgetExceededError(
                f"Budget esgotado: ${self.total_cost_usd:.4f} >= ${BUDGET_USD:.2f}"
            )

    def warn_budget(self) -> str | None:
        if self.total_cost_usd >= BUDGET_WARN_USD:
            return (
                f"⚠️ Budget em {self.total_cost_usd / BUDGET_USD * 100:.0f}%: "
                f"${self.total_cost_usd:.4f} / ${BUDGET_USD:.2f}"
            )
        return None

    def add_turn(
        self,
        role: Role,
        content: str,
        agent_id: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self.turns.append(
            Turn(
                role=role,
                content=content,
                agent_id=agent_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
            )
        )

    def recent_context(self, max_turns: int = 6) -> str:
        lines = []
        for t in self.turns[-max_turns:]:
            tag = t.agent_id or t.role
            lines.append(f"[{tag}] {t.content[:600]}")
        return "\n".join(lines)
