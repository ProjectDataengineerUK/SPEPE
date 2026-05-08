"""Real Python tools executed by the Supervisor — not by LLM sub-agents."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Literal, cast

from pydantic import BaseModel, field_validator

logger = logging.getLogger("spepe.agents.tools")

JobName = Literal[
    # TSE
    "tse_ingest", "tse_perfil_ingest", "tse_candidaturas_ingest",
    # IBGE / Public data
    "ibge_sync", "datasus_ingest", "security_ingest", "cetic_ingest",
    "dieese_ingest", "endividamento_ingest", "cadunico_ingest",
    # Social / Digital
    "digital_ingest", "social_ingest", "reddit_ingest",
    # Political / Fiscal
    "pesquisas_ingest", "camara_senado_ingest",
    "emendas_ingest", "sancoes_ingest",
    # Pipeline
    "silver_transform", "gold_build",
    # Discovery
    "candidatos_discovery",
]

ALLOWED_JOBS: dict[JobName, str] = {
    # TSE
    "tse_ingest": "dataops/jobs/tse_ingest_job.py",
    "tse_perfil_ingest": "dataops/jobs/tse_perfil_ingest_job.py",
    "tse_candidaturas_ingest": "dataops/jobs/tse_candidaturas_ingest_job.py",
    # IBGE / Public data
    "ibge_sync": "dataops/jobs/ibge_sync_job.py",
    "datasus_ingest": "dataops/jobs/datasus_ingest_job.py",
    "security_ingest": "dataops/jobs/security_ingest_job.py",
    "cetic_ingest": "dataops/jobs/cetic_ingest_job.py",
    "dieese_ingest": "dataops/jobs/dieese_ingest_job.py",
    "endividamento_ingest": "dataops/jobs/endividamento_ingest_job.py",
    "cadunico_ingest": "dataops/jobs/cadunico_ingest_job.py",
    # Social / Digital
    "digital_ingest": "dataops/jobs/digital_ingest_job.py",
    "social_ingest": "dataops/jobs/social_ingest_job.py",
    "reddit_ingest": "dataops/jobs/reddit_ingest_job.py",
    # Political / Fiscal
    "pesquisas_ingest": "dataops/jobs/pesquisas_ingest_job.py",
    "camara_senado_ingest": "dataops/jobs/camara_senado_ingest_job.py",
    "emendas_ingest": "dataops/jobs/emendas_ingest_job.py",
    "sancoes_ingest": "dataops/jobs/sancoes_ingest_job.py",
    # Pipeline
    "silver_transform": "dataops/jobs/silver_transform_job.py",
    "gold_build": "dataops/jobs/gold_build_job.py",
    # Discovery
    "candidatos_discovery": "dataops/jobs/candidatos_discovery_job.py",
}

CLOUD_RUN_JOBS: dict[JobName, str] = {
    # TSE
    "tse_ingest": "spepe-tse-ingest",
    "tse_perfil_ingest": "spepe-tse-perfil-ingest",
    "tse_candidaturas_ingest": "spepe-tse-candidaturas-ingest",
    # IBGE / Public data
    "ibge_sync": "spepe-ibge-sync",
    "datasus_ingest": "spepe-datasus-ingest",
    "security_ingest": "spepe-security-ingest",
    "cetic_ingest": "spepe-cetic-ingest",
    "dieese_ingest": "spepe-dieese-ingest",
    "endividamento_ingest": "spepe-endividamento-ingest",
    "cadunico_ingest": "spepe-cadunico-ingest",
    # Social / Digital
    "digital_ingest": "spepe-digital-ingest",
    "social_ingest": "spepe-social-ingest",
    "reddit_ingest": "spepe-reddit-ingest",
    # Political / Fiscal
    "pesquisas_ingest": "spepe-pesquisas-ingest",
    "camara_senado_ingest": "spepe-camara-senado-ingest",
    "emendas_ingest": "spepe-emendas-ingest",
    "sancoes_ingest": "spepe-sancoes-ingest",
    # Pipeline
    "silver_transform": "spepe-silver-transform",
    "gold_build": "spepe-gold-build",
    # Discovery
    "candidatos_discovery": "spepe-candidatos-discovery",
}


class RunJobArgs(BaseModel):
    job: str
    uf: str = "BR"
    year: int = 2022

    @field_validator("job")
    @classmethod
    def validate_job(cls, v: str) -> str:
        if v not in ALLOWED_JOBS:
            raise ValueError(f"Job não permitido: {v}. Permitidos: {list(ALLOWED_JOBS)}")
        return v

    @field_validator("uf")
    @classmethod
    def validate_uf(cls, v: str) -> str:
        if v != "BR" and not (len(v) == 2 and v.isalpha()):
            raise ValueError(f"UF inválida: {v}")
        return v.upper()

    @field_validator("year")
    @classmethod
    def validate_year(cls, v: int) -> int:
        if not 2010 <= v <= 2030:
            raise ValueError(f"Ano fora do intervalo 2010-2030: {v}")
        return v


def run_dataops_job(args: RunJobArgs) -> dict:
    """Execute a dataops job. In GCP: triggers Cloud Run Job via API. Locally: subprocess."""
    project = os.environ.get("GCP_PROJECT_ID", "")
    region = os.environ.get("VERTEX_LOCATION", "us-central1")

    if project:
        return _run_cloud_run_job(args, project, region)
    return _run_local_subprocess(args)


def _run_cloud_run_job(args: RunJobArgs, project: str, region: str) -> dict:
    try:
        from google.cloud import run_v2

        client = run_v2.JobsClient()
        job_name = (
            f"projects/{project}/locations/{region}/jobs/{CLOUD_RUN_JOBS[cast(JobName, args.job)]}"
        )

        request = run_v2.RunJobRequest(
            name=job_name,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(
                        env=[
                            run_v2.EnvVar(name="DEFAULT_UF", value=args.uf),
                            run_v2.EnvVar(name="DEFAULT_YEAR", value=str(args.year)),
                        ]
                    )
                ]
            ),
        )
        operation = client.run_job(request=request)
        logger.info("Cloud Run Job disparado: %s (uf=%s year=%s)", args.job, args.uf, args.year)
        return {
            "ok": True,
            "mode": "cloud_run",
            "job": args.job,
            "operation": (operation.metadata.name if operation.metadata else "dispatched"),
        }
    except Exception as e:
        logger.error("Cloud Run Job falhou: %s — fallback local", e)
        return _run_local_subprocess(args)


def _run_local_subprocess(args: RunJobArgs) -> dict:
    import sys

    script = ALLOWED_JOBS[cast(JobName, args.job)]
    cmd = [sys.executable, script, "--uf", args.uf, "--year", str(args.year)]
    logger.info("Executando local: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return {
            "ok": result.returncode == 0,
            "mode": "local",
            "job": args.job,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "mode": "local",
            "job": args.job,
            "error": "Timeout 600s excedido",
        }
    except Exception as e:
        return {"ok": False, "mode": "local", "job": args.job, "error": str(e)}


RUN_JOB_TOOL_SCHEMA: dict = {
    "name": "run_dataops_job",
    "description": (
        "Executa um job real de dataops (ingestão TSE/IBGE, transformação Silver, "
        "build Gold). Em produção dispara Cloud Run Job; em dev executa subprocess local."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "job": {
                "type": "string",
                "enum": list(ALLOWED_JOBS.keys()),
                "description": "Nome do job a executar",
            },
            "uf": {
                "type": "string",
                "description": "UF de 2 letras (ex: SP, MG) ou BR para todos os estados",
                "default": "BR",
            },
            "year": {
                "type": "integer",
                "minimum": 2010,
                "maximum": 2030,
                "description": "Ano eleitoral",
                "default": 2022,
            },
        },
        "required": ["job"],
    },
}
