"""Vertex AI HyperparameterTuningJob for SPEPE ML models."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HPTuningResult:
    best_params: dict[str, Any]
    best_metric_value: float
    trial_id: str
    n_trials_completed: int


def run_hptuning(
    project_id: str | None = None,
    location: str | None = None,
    staging_bucket: str | None = None,
    max_trial_count: int = 10,
    parallel_trial_count: int = 3,
) -> HPTuningResult:
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    location = location or os.environ.get("VERTEX_LOCATION", "us-central1")
    staging_bucket = (
        staging_bucket or f"gs://{os.environ.get('GCS_BUCKET', 'spepe-dev-data')}/hptuning"
    )

    try:
        from google.cloud import aiplatform
        from google.cloud.aiplatform import hyperparameter_tuning as hpt

        aiplatform.init(project=project_id, location=location, staging_bucket=staging_bucket)

        worker_pool_specs = [
            {
                "machine_spec": {"machine_type": "n1-standard-4"},
                "replica_count": 1,
                "container_spec": {
                    "image_uri": f"gcr.io/{project_id}/spepe:latest",
                    "command": ["python", "-m", "mlops.components.train_bootstrap"],
                    "args": [
                        "--hp-tune",
                        "--min-cluster-size",
                        hpt.IntegerParameterSpec(min=20, max=50, scale="linear"),
                        "--n-bootstrap",
                        hpt.IntegerParameterSpec(min=500, max=2000, scale="log"),
                        "--silhouette-thr",
                        hpt.DoubleParameterSpec(min=0.35, max=0.55, scale="linear"),
                    ],
                },
            }
        ]

        hp_job = aiplatform.HyperparameterTuningJob(  # type: ignore[call-arg]
            display_name="spepe-hptuning",
            metric_spec={"brier_score": "minimize"},
            parameter_spec={
                "min_cluster_size": hpt.IntegerParameterSpec(min=20, max=50, scale="linear"),
                "n_bootstrap": hpt.IntegerParameterSpec(min=500, max=2000, scale="log"),
                "silhouette_thr": hpt.DoubleParameterSpec(min=0.35, max=0.55, scale="linear"),
            },
            max_trial_count=max_trial_count,
            parallel_trial_count=parallel_trial_count,
            worker_pool_specs=worker_pool_specs,
        )

        hp_job.run(sync=True)

        best = hp_job.trials[0]
        best_params = {p.parameter_id: p.value for p in best.parameters}
        return HPTuningResult(
            best_params=best_params,
            best_metric_value=best.final_measurement.metrics[0].value,
            trial_id=best.id,
            n_trials_completed=len(hp_job.trials),
        )

    except Exception as exc:
        logger.warning("HyperparameterTuningJob failed: %s — returning defaults", exc)
        return HPTuningResult(
            best_params={
                "min_cluster_size": 30,
                "n_bootstrap": 1000,
                "silhouette_thr": 0.45,
            },
            best_metric_value=float("inf"),
            trial_id="default",
            n_trials_completed=0,
        )
