"""Cloud Run Job: compile and submit the SPEPE Vertex AI ML pipeline."""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.run_pipeline")

PIPELINE_YAML = os.environ.get("PIPELINE_YAML_PATH", "/tmp/spepe_pipeline.yaml")


def main() -> None:
    project_id = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    vertex_location = os.environ.get("VERTEX_LOCATION", "us-central1")
    gcs_bucket = os.environ.get("GCS_BUCKET", f"{project_id}-data")
    pipeline_root = f"gs://{gcs_bucket}/vertex-pipelines"

    cd_cargo = int(os.environ.get("CD_CARGO", "3"))
    ano_eleicao = int(os.environ.get("ANO_ELEICAO", "2022"))
    n_bootstrap = int(os.environ.get("N_BOOTSTRAP", "1000"))
    mae_threshold = float(os.environ.get("MAE_THRESHOLD", "0.10"))
    job_display_name = os.environ.get(
        "JOB_DISPLAY_NAME",
        f"spepe-ml-{ano_eleicao}-cargo{cd_cargo}",
    )

    logger.info(
        "Compilando pipeline: project=%s  location=%s  cargo=%d  ano=%d",
        project_id,
        vertex_location,
        cd_cargo,
        ano_eleicao,
    )

    try:
        from mlops.vertex_pipeline import compile_pipeline, submit_pipeline
    except ImportError as exc:
        logger.error("Falha ao importar vertex_pipeline: %s", exc)
        sys.exit(1)

    try:
        compiled_path = compile_pipeline(PIPELINE_YAML)
        logger.info("Pipeline compilado: %s", compiled_path)
    except Exception as exc:
        logger.error("Falha na compilação do pipeline: %s", exc)
        sys.exit(1)

    parameters: dict = {
        "project_id": project_id,
        "gold_dataset": "spepe_gold",
        "gold_table": "fact_municipio_candidato_eleicao",
        "n_bootstrap": n_bootstrap,
        "cd_cargo": cd_cargo,
        "ano_eleicao": ano_eleicao,
        "mae_threshold": mae_threshold,
    }

    try:
        resource_name = submit_pipeline(
            pipeline_yaml=compiled_path,
            parameters=parameters,
            project_id=project_id,
            location=vertex_location,
            pipeline_root=pipeline_root,
            job_display_name=job_display_name,
        )
        logger.info("Pipeline submetido com sucesso: %s", resource_name)
    except Exception as exc:
        logger.error("Falha ao submeter pipeline: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
