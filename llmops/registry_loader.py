from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class RegistryLoader:
    """Loads versioned prompts from config/prompt_registry by agent + version."""

    def __init__(self, registry_root: str | Path = "config/prompt_registry"):
        self.root = Path(registry_root)

    def load(self, agent_name: str, version: str = "latest") -> str:
        agent_dir = self.root / agent_name
        if not agent_dir.exists():
            logger.warning("agent_prompt_dir_missing: %s", agent_dir)
            fallback = Path("agents/registry") / f"{agent_name}.md"
            if fallback.exists():
                return fallback.read_text(encoding="utf-8")
            return ""
        if version == "latest":
            versions = sorted(p for p in agent_dir.iterdir() if p.suffix in {".md", ".txt"})
            if not versions:
                return ""
            return versions[-1].read_text(encoding="utf-8")
        path = agent_dir / f"{version}.md"
        if not path.exists():
            path = agent_dir / f"{version}.txt"
        if not path.exists():
            logger.warning("prompt_version_missing: %s v=%s", agent_name, version)
            return ""
        return path.read_text(encoding="utf-8")

    def list_versions(self, agent_name: str) -> list[str]:
        agent_dir = self.root / agent_name
        if not agent_dir.exists():
            return []
        return sorted(p.stem for p in agent_dir.iterdir() if p.suffix in {".md", ".txt"})
