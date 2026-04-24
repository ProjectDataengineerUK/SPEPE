"""Sentinel — autonomous monitoring orchestrator for SPEPE.

A fleet of AI agents organized as 4 crews:
- Observadores: detect events from DataOps, MLOps, Infra, Social
- Analisadores: detect patterns and cross-domain correlations
- Interpretadores: enrich with KB context and run GenAI root-cause analysis
- Despachantes: report, dispatch alerts and execute approved actions

Orthogonal to the Supervisor: the Supervisor serves users, the Sentinel
watches the system 24/7.
"""

from sentinel.orchestrator import SentinelOrchestrator

__all__ = ["SentinelOrchestrator"]
