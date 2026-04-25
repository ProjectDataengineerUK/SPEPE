import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("spepe.hooks.audit")

_AUDIT_DIR = Path("output/audit")
_audit_file: Path | None = None
_cloud_logging_client = None


def _get_audit_file() -> Path:
    global _audit_file
    if _audit_file is None:
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        _audit_file = _AUDIT_DIR / f"audit_{ts}.jsonl"
    return _audit_file


def _get_cloud_logger():
    global _cloud_logging_client
    project = os.environ.get("GCP_PROJECT_ID", "")
    if not project:
        return None
    if _cloud_logging_client is None:
        try:
            from google.cloud import logging as cloud_logging

            client = cloud_logging.Client(project=project)
            _cloud_logging_client = client.logger("spepe-audit")
        except Exception:
            _cloud_logging_client = False
    return _cloud_logging_client if _cloud_logging_client else None


def audit_tool_usage(
    tool_name: str,
    tool_input: dict,
    result: str | None = None,
    session_id: str = "",
    agent_name: str = "",
) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool_name,
        "input_keys": list(tool_input.keys()),
        "result_len": len(result) if result else 0,
        "session_id": session_id,
        "agent": agent_name,
    }

    try:
        with open(_get_audit_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"Audit write failed: {e}")

    cloud_logger = _get_cloud_logger()
    if cloud_logger:
        try:
            cloud_logger.log_struct(entry, severity="INFO")
        except Exception:
            pass
