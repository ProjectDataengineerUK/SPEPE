"""Load agent registry (agents/registry/*.md) and instantiate GeminiAgents."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agents.gemini_agent import GeminiAgent

REGISTRY_DIR = Path(__file__).parent / "registry"

_MODEL_ASSIGNMENT = {
    "coletor": "gemini-2.5-flash",
    "perfilador": "gemini-2.5-flash",
    "analista": "gemini-2.5-pro",
    "analista_seguranca": "gemini-2.5-pro",
    "modelista_bayesiano": "gemini-2.5-pro",
    "explicador": "gemini-2.5-pro",
    "narrador": "gemini-2.0-flash",
    "vigilante": "gemini-2.0-flash",
    "sentinela_social": "gemini-2.0-flash",
    "contextualizador_saude": "gemini-2.5-flash",
}

_ID_ALIASES = {
    "analista-eleitoral": "analista",
    "analista_eleitoral": "analista",
    "analista-seguranca": "analista_seguranca",
    "modelista-bayesiano": "modelista_bayesiano",
}


def _parse_md(path: Path) -> tuple[dict, str]:
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if not match:
        return {}, content
    meta = yaml.safe_load(match.group(1)) or {}
    return meta, match.group(2).strip()


def load_agents() -> dict[str, GeminiAgent]:
    agents: dict[str, GeminiAgent] = {}
    for md_path in sorted(REGISTRY_DIR.glob("*.md")):
        meta, system_prompt = _parse_md(md_path)
        raw_id = meta.get("name", md_path.stem)
        agent_id = _ID_ALIASES.get(raw_id, raw_id.replace("-", "_"))
        model = _MODEL_ASSIGNMENT.get(agent_id, "gemini-2.5-flash")
        agents[agent_id] = GeminiAgent(
            agent_id=agent_id,
            name=meta.get("name", agent_id),
            model=model,
            system_prompt=system_prompt,
        )
    return agents
