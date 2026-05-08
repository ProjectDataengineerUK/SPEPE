"""Gemini sub-agent wrapper — stateful via session context injection."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from config.settings import settings

logger = logging.getLogger("spepe.agents.gemini")

_VERTEX_MODEL_IDS = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.0-flash": "gemini-2.0-flash-001",
}

_COST_PER_TOKEN = {
    "gemini-2.5-pro": (1.25e-6, 10e-6),
    "gemini-2.5-flash": (0.15e-6, 0.60e-6),
    "gemini-2.0-flash-001": (0.075e-6, 0.30e-6),
}


@dataclass
class AgentResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def cost_usd(self) -> float:
        in_rate, out_rate = _COST_PER_TOKEN.get(self.model, (0.0, 0.0))
        return self.input_tokens * in_rate + self.output_tokens * out_rate


class GeminiAgent:
    def __init__(self, agent_id: str, name: str, model: str, system_prompt: str):
        self.agent_id = agent_id
        self.name = name
        self.model = _VERTEX_MODEL_IDS.get(model, model)
        self.system_prompt = system_prompt
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                vertexai=True,
                project=settings.gcp_project_id or "spepe-dev",
                location=settings.vertex_location,
            )
        return self._client

    def _build_prompt(
        self,
        prompt: str,
        session_ctx: str | None = None,
        artifacts: dict[str, str] | None = None,
    ) -> str:
        parts = []
        if session_ctx:
            parts.append(f"## Contexto da sessão (turnos recentes)\n{session_ctx}")
        if artifacts:
            refs = "\n".join(f"- {k}: {v}" for k, v in artifacts.items())
            parts.append(f"## Artefatos disponíveis nesta sessão\n{refs}")
        parts.append(f"## Sua tarefa\n{prompt}")
        return "\n\n".join(parts)

    def run(
        self,
        prompt: str,
        session_ctx: str | None = None,
        artifacts: dict[str, str] | None = None,
    ) -> AgentResponse:
        from google.genai import types

        full_prompt = self._build_prompt(prompt, session_ctx, artifacts)
        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.3,
            max_output_tokens=4096,
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=full_prompt,
            config=config,
        )
        usage = response.usage_metadata
        return AgentResponse(
            text=response.text or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
            model=self.model,
        )

    async def run_async(
        self,
        prompt: str,
        session_ctx: str | None = None,
        artifacts: dict[str, str] | None = None,
    ) -> AgentResponse:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run, prompt, session_ctx, artifacts)
