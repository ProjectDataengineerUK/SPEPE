"""Cloud Run canary deployment: 10% challenger traffic → evaluate → promote or rollback."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CANARY_TRAFFIC_PCT = 10
EVAL_WINDOW_HOURS = 48
CHECK_INTERVAL_SECONDS = 6 * 3600  # 6h


@dataclass
class CanaryState:
    service: str
    champion_revision: str
    challenger_revision: str
    project_id: str
    region: str
    status: str = "running"  # running | promoted | rolled_back


def start_canary(
    service: str,
    challenger_revision: str,
    project_id: str | None = None,
    region: str | None = None,
) -> CanaryState:
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    region = region or os.environ.get("GCP_REGION", "southamerica-east1")

    champion_revision = _get_latest_serving_revision(service, project_id, region)

    _set_traffic_split(
        service=service,
        project_id=project_id,
        region=region,
        splits={
            challenger_revision: CANARY_TRAFFIC_PCT,
            champion_revision: 100 - CANARY_TRAFFIC_PCT,
        },
    )
    logger.info(
        "Canary started: %d%% → %s, %d%% → %s",
        CANARY_TRAFFIC_PCT, challenger_revision,
        100 - CANARY_TRAFFIC_PCT, champion_revision,
    )

    return CanaryState(
        service=service,
        champion_revision=champion_revision,
        challenger_revision=challenger_revision,
        project_id=project_id,
        region=region,
    )


def promote_canary(state: CanaryState) -> None:
    _set_traffic_split(
        service=state.service,
        project_id=state.project_id,
        region=state.region,
        splits={state.challenger_revision: 100},
    )
    state.status = "promoted"
    logger.info("Canary PROMOTED: %s → 100%% traffic", state.challenger_revision)


def rollback_canary(state: CanaryState) -> None:
    _set_traffic_split(
        service=state.service,
        project_id=state.project_id,
        region=state.region,
        splits={state.champion_revision: 100},
    )
    state.status = "rolled_back"
    logger.warning("Canary ROLLED BACK: %s → 100%% traffic restored", state.champion_revision)


def _get_latest_serving_revision(service: str, project_id: str, region: str) -> str:
    result = subprocess.run(
        [
            "gcloud", "run", "services", "describe", service,
            "--project", project_id, "--region", region,
            "--format", "value(status.traffic[0].revisionName)",
        ],
        capture_output=True, text=True, timeout=30,
    )
    revision = result.stdout.strip()
    if not revision:
        raise RuntimeError(f"Cannot determine current serving revision for {service}")
    return revision


def _set_traffic_split(
    service: str,
    project_id: str,
    region: str,
    splits: dict[str, int],
) -> None:
    traffic_args = ",".join(f"{rev}={pct}" for rev, pct in splits.items())
    cmd = [
        "gcloud", "run", "services", "update-traffic", service,
        "--project", project_id,
        "--region", region,
        "--to-revisions", traffic_args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Traffic split failed: {result.stderr}")
