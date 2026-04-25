"""Cost guard — stateless classifier. Budget tracking moved to SessionState."""

import logging

logger = logging.getLogger("spepe.hooks.cost_guard")

_HIGH_COST_TOOLS = {"tse_ingest", "ibge_sync", "digital_ingest"}
_MEDIUM_COST_TOOLS = {"silver_transform", "gold_build"}


def log_cost_generating_operations(tool_name: str, tool_input: dict) -> str | None:
    if tool_name in _HIGH_COST_TOOLS:
        logger.info("HIGH cost op: %s", tool_name)
        return None
    if tool_name in _MEDIUM_COST_TOOLS:
        logger.debug("MEDIUM cost op: %s", tool_name)
    return None
