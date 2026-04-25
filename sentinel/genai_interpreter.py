from __future__ import annotations

import json
import logging
import re
from typing import Any

from sentinel.kb.context_builder import SentinelContext

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """Voce e o Interpretador do Sentinel SPEPE. Dado o evento,
o contexto de KB historica e as correlacoes abaixo, identifique a causa raiz
provavel e a acao recomendada.

{context}

Responda APENAS em JSON no formato:
{{
  "causa_raiz": "...",
  "confianca": 0.0-1.0,
  "acao_recomendada": "...",
  "severidade": "P1|P2|P3",
  "referencias_kb": ["id1", "id2"],
  "requer_humano": true|false
}}
"""


class GenAIInterpreter:
    """Claude Sonnet interpreter: given context, returns root cause + action."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        max_tokens: int = 1024,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self._client = self._init_client(api_key)

    def _init_client(self, api_key: str | None):
        try:
            from anthropic import Anthropic

            return Anthropic(api_key=api_key) if api_key else Anthropic()
        except Exception as exc:
            logger.info("anthropic_client_unavailable: %s", exc)
            return None

    def interpret(self, context: SentinelContext) -> dict[str, Any]:
        if self._client is None:
            return self._fallback(context)
        prompt = PROMPT_TEMPLATE.format(context=context.to_prompt())
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text if msg.content else ""
            return self._parse_json(text) or self._fallback(context)
        except Exception as exc:
            logger.exception("genai_interpret_failed: %s", exc)
            return self._fallback(context)

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def _fallback(self, context: SentinelContext) -> dict[str, Any]:
        best = context.patterns[0] if context.patterns else None
        if best:
            return {
                "causa_raiz": best.get("probable_cause", "unknown"),
                "confianca": float(best.get("confidence", 0.5)),
                "acao_recomendada": best.get("recommended_action", "requires_human_review"),
                "severidade": context.event.get("severity", "P2"),
                "referencias_kb": [best.get("pattern_id")],
                "requer_humano": float(best.get("confidence", 0.5)) < 0.70,
            }
        return {
            "causa_raiz": "no_kb_match",
            "confianca": 0.30,
            "acao_recomendada": "requires_human_review",
            "severidade": context.event.get("severity", "P2"),
            "referencias_kb": [],
            "requer_humano": True,
        }
