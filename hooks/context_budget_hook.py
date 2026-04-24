import logging

logger = logging.getLogger("spepe.hooks.context_budget")

_CONTEXT_LIMIT = 200_000
_WARN_80 = int(_CONTEXT_LIMIT * 0.80)
_WARN_95 = int(_CONTEXT_LIMIT * 0.95)
_warned_80 = False
_warned_95 = False


def track_context_budget(tokens_used: int) -> str | None:
    global _warned_80, _warned_95

    if tokens_used >= _WARN_95 and not _warned_95:
        _warned_95 = True
        msg = f"AVISO CRÍTICO: contexto em 95% ({tokens_used:,}/{_CONTEXT_LIMIT:,} tokens). Considere encerrar a sessão."
        logger.warning(msg)
        return msg

    if tokens_used >= _WARN_80 and not _warned_80:
        _warned_80 = True
        msg = f"Aviso: contexto em 80% ({tokens_used:,}/{_CONTEXT_LIMIT:,} tokens)."
        logger.warning(msg)
        return msg

    return None


def reset_context_warnings() -> None:
    global _warned_80, _warned_95
    _warned_80 = False
    _warned_95 = False
