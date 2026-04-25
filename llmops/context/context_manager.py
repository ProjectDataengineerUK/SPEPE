from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ContextSnapshot:
    used_tokens: int
    max_tokens: int
    fill_rate: float
    summarized: bool = False
    preserved_sections: list[str] = field(default_factory=list)


class ContextManager:
    """Auto-summarizes context at 80% fill rate, preserving critical sections."""

    PRESERVE_KEYWORDS = (
        "decisao",
        "conclusao",
        "/coletar",
        "/analisar",
        "/prever",
        "/explicar",
        "/relatorio",
        "uf=",
        "ano=",
        "candidato=",
    )

    def __init__(self, max_tokens: int = 200_000, trigger_fill_rate: float = 0.80):
        self.max_tokens = max_tokens
        self.trigger_fill_rate = trigger_fill_rate

    def approx_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def should_summarize(self, text: str) -> bool:
        return self.approx_tokens(text) / self.max_tokens >= self.trigger_fill_rate

    def summarize(
        self, messages: list[dict[str, str]]
    ) -> tuple[list[dict[str, str]], ContextSnapshot]:
        preserved: list[dict[str, str]] = []
        regular: list[dict[str, str]] = []
        for m in messages:
            content = (m.get("content") or "").lower()
            if any(kw in content for kw in self.PRESERVE_KEYWORDS):
                preserved.append(m)
            else:
                regular.append(m)
        summary_text = self._compact(regular)
        new_messages: list[dict[str, str]] = preserved.copy()
        if summary_text:
            new_messages.append({"role": "system", "content": f"[context_summary] {summary_text}"})
        used = sum(self.approx_tokens(m["content"]) for m in new_messages)
        snapshot = ContextSnapshot(
            used_tokens=used,
            max_tokens=self.max_tokens,
            fill_rate=used / self.max_tokens,
            summarized=True,
            preserved_sections=[m.get("role", "?") for m in preserved],
        )
        return new_messages, snapshot

    def _compact(self, messages: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content", "")
            short = content if len(content) < 200 else f"{content[:180]}..."
            lines.append(f"{role}: {short}")
        return " | ".join(lines[-25:])
