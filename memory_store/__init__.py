"""Vertex AI Vector Search — long-term agent memory.

Five memory types stored per agent namespace. Gecko 768d embeddings, ANN ScaNN,
TTL 1 year, K=5 retrieval above cosine similarity 0.75.
"""

from memory_store.memory_types import MemoryType, Memory
from memory_store.retriever import retrieve_relevant_memories
from memory_store.session_memory import SessionMemory
from memory_store.memory_manager import MemoryManager

__all__ = [
    "Memory",
    "MemoryType",
    "MemoryManager",
    "SessionMemory",
    "retrieve_relevant_memories",
]
