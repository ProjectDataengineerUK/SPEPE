import logging

logger = logging.getLogger("spepe.hooks.output_compressor")

_MAX_OUTPUT_CHARS = 8000
_TRUNCATION_MSG = (
    "\n\n[... output truncado para economizar contexto. Use filtros mais específicos ...]"
)


def compress_tool_output(tool_name: str, output: str) -> str:
    if len(output) <= _MAX_OUTPUT_CHARS:
        return output
    logger.debug(f"Comprimindo output de {tool_name}: {len(output)} → {_MAX_OUTPUT_CHARS} chars")
    return output[:_MAX_OUTPUT_CHARS] + _TRUNCATION_MSG
