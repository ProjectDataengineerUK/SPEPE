#!/usr/bin/env python3
"""Submit SPEPE ML Pipeline to Vertex AI (KFP 2.x)"""

import os
import sys
from datetime import datetime

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", f"{GCP_PROJECT}-data")
PIPELINE_ROOT = f"gs://{GCS_BUCKET}/vertex-pipelines"

print("[1/3] Compiling KFP 2.x pipeline...")

try:
    from kfp import compiler
    from mlops.vertex_pipeline import build_pipeline

    pipeline_func = build_pipeline()
    pipeline_yaml = "output/spepe-ml-pipeline.yaml"
    compiler.Compiler().compile(pipeline_func, pipeline_yaml)
    print(f"OK - Pipeline compiled to {pipeline_yaml}")

except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

print("\n[2/3] Submitting to Vertex AI...")

try:
    from google.cloud import aiplatform

    aiplatform.init(project=GCP_PROJECT, location=VERTEX_LOCATION)

    job = aiplatform.PipelineJob(
        display_name=f"spepe-ml-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        template_path=pipeline_yaml,
        pipeline_root=PIPELINE_ROOT,
        parameter_values={
            "project_id": GCP_PROJECT,
            "gold_dataset": "spepe_gold",
            "gold_table": "fact_municipio_eleicao",
            "cd_cargo": 1,
            "ano_eleicao": 2026,
            "n_bootstrap": 1000,
            "test_size": 0.2,
            "mae_threshold": 5.0,
        },
    )

    job.submit(sync=False)
    print(f"OK - Job submitted: {job.name}")

except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

print("\n[3/3] Monitoring execution...")
print(f"Project: {GCP_PROJECT}")
print(f"Location: {VERTEX_LOCATION}")

try:
    job.wait()

    if job.state.name == "PIPELINE_STATE_SUCCEEDED":
        print("\nSUCCESS - Pipeline completed!")
        print(f"Job ID: {job.name}")
    else:
        print(f"\nFAILED - Status: {job.state.name}")
        sys.exit(1)

except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
