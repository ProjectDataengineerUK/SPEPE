"""Load agent prompts from versioned YAML registry."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger("spepe.mlops.registry")

REGISTRY_DIR = Path(__file__).parent.parent.parent / "config" / "prompt_registry"


def load_prompt(agent_name: str, version: str | None = None) -> dict:
    """Load prompt config for an agent by name and optional version."""
    if version:
        path = REGISTRY_DIR / f"{agent_name}_v{version}.yaml"
    else:
        files = sorted(REGISTRY_DIR.glob(f"{agent_name}_v*.yaml"), reverse=True)
        if not files:
            raise FileNotFoundError(f"Nenhum prompt encontrado para agente: {agent_name}")
        path = files[0]

    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.debug(f"Prompt carregado: {path.name}")
    return config


def get_system_prompt(agent_name: str, version: str | None = None, **variables) -> str:
    config = load_prompt(agent_name, version)
    prompt = config.get("system_prompt", "")
    for key, value in variables.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt


def list_available_prompts() -> list[dict]:
    result = []
    for path in sorted(REGISTRY_DIR.glob("*_v*.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            result.append({
                "file": path.name,
                "name": config.get("name", ""),
                "version": config.get("version", ""),
                "model": config.get("model", ""),
                "description": config.get("description", ""),
            })
        except Exception as e:
            logger.warning(f"Erro ao ler {path}: {e}")
    return result
