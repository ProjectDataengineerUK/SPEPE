from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Executes approved autonomous actions with cooldown enforcement."""

    DEFAULT_COOLDOWNS = {
        "retrain": timedelta(hours=72),
        "rollback": timedelta(hours=24),
        "pipeline_retry": timedelta(minutes=30),
        "scale_job": timedelta(hours=1),
        "block_pipeline": timedelta(hours=1),
    }

    def __init__(
        self,
        auto_actions_enabled: dict[str, bool] | None = None,
        cooldowns: dict[str, timedelta] | None = None,
        action_handlers: dict[str, Callable[[dict], dict]] | None = None,
    ):
        self.auto_actions_enabled = auto_actions_enabled or {}
        self.cooldowns = {**self.DEFAULT_COOLDOWNS, **(cooldowns or {})}
        self.handlers = action_handlers or {}
        self._last_executed: dict[str, datetime] = {}

    def execute(
        self, action: str, event_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.auto_actions_enabled.get(event_type, False):
            return {
                "executed": False,
                "reason": "auto_actions_disabled_for_event",
                "action": action,
            }
        cooldown = self.cooldowns.get(action, timedelta(minutes=15))
        last = self._last_executed.get(action)
        now = datetime.now(timezone.utc)
        if last is not None and (now - last) < cooldown:
            remaining = cooldown - (now - last)
            return {
                "executed": False,
                "reason": f"cooldown_active remaining={remaining}",
                "action": action,
            }
        handler = self.handlers.get(action)
        if handler is None:
            logger.warning("no_handler_for_action=%s", action)
            return {
                "executed": False,
                "reason": "no_handler_registered",
                "action": action,
            }
        try:
            result = handler(params or {})
            self._last_executed[action] = now
            return {"executed": True, "action": action, "result": result}
        except Exception as exc:
            logger.exception("action_handler_failed: %s", exc)
            return {"executed": False, "action": action, "error": str(exc)}
