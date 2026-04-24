from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class MemoryType(str, Enum):
    ANALISE = "analise"
    PADRAO_ELEITORAL = "padrao_eleitoral"
    ALERTA = "alerta"
    DECISAO_MODELO = "decisao_modelo"
    CONTEXTO_POLITICO = "contexto_politico"


@dataclass
class Memory:
    content: str
    memory_type: MemoryType
    agent_name: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    session_id: str | None = None
    similarity: float | None = None
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "memory_type": self.memory_type.value
            if isinstance(self.memory_type, MemoryType)
            else self.memory_type,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "similarity": self.similarity,
            "metadata": self.metadata or {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Memory":
        mt = data.get("memory_type")
        if isinstance(mt, str):
            mt_enum = MemoryType(mt)
        else:
            mt_enum = mt or MemoryType.ANALISE
        return cls(
            content=data["content"],
            memory_type=mt_enum,
            agent_name=data.get("agent_name", "unknown"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            session_id=data.get("session_id"),
            similarity=data.get("similarity"),
            metadata=data.get("metadata"),
        )
