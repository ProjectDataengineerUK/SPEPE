"""Vertex AI Pipeline (KFP v2) — SPEPE ML pipeline definition.

Pipeline: feature_extract → train_bootstrap → evaluate → promote
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("spepe.mlops.vertex_pipeline")

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", f"{GCP_PROJECT}-pipelines")
PIPELINE_ROOT = f"gs://{GCS_BUCKET}/pipeline-runs"


def build_pipeline():
    """Build the KFP v2 pipeline definition."""
    try:
        from kfp import dsl
        from kfp.dsl import component, Input, Output, Dataset, Model, Metrics
    except ImportError:
        raise ImportError("kfp não instalado. Execute: pip install kfp")

    @component(
        base_image="python:3.12-slim",
        packages_to_install=["google-cloud-bigquery", "pandas", "pyarrow", "scikit-learn"],
    )
    def extract_features_component(
        project_id: str,
        gold_dataset: str,
        gold_table: str,
        output_dataset: Output[Dataset],
    ) -> None:
        from google.cloud import bigquery
        client = bigquery.Client(project=project_id)
        query = f"SELECT * FROM `{project_id}.{gold_dataset}.{gold_table}`"
        df = client.query(query).to_dataframe()
        df.to_parquet(output_dataset.path, index=False)

    @component(
        base_image="python:3.12-slim",
        packages_to_install=["pandas", "pyarrow", "statsmodels", "scikit-learn", "numpy"],
    )
    def train_bootstrap_component(
        input_dataset: Input[Dataset],
        n_bootstrap: int,
        target_candidate: str,
        output_model: Output[Model],
        output_metrics: Output[Metrics],
    ) -> None:
        import pandas as pd
        import pickle
        from sklearn.linear_model import LogisticRegression

        df = pd.read_parquet(input_dataset.path)
        feature_cols = [c for c in df.columns if any(ind in c for ind in ["idhm", "renda", "estudo", "pct"])]
        target_col = f"pct_votos_{target_candidate.lower().replace(' ', '_')}_2022"

        if target_col not in df.columns or not feature_cols:
            return

        X = df[feature_cols].fillna(0).values
        y = (df[target_col] > 0.5).astype(int).values

        model = LogisticRegression(max_iter=500, random_state=42)
        model.fit(X, y)

        with open(output_model.path, "wb") as f:
            pickle.dump({"model": model, "feature_cols": feature_cols}, f)

        from sklearn.metrics import accuracy_score
        output_metrics.log_metric("accuracy", float(accuracy_score(y, model.predict(X))))
        output_metrics.log_metric("n_train", len(X))

    @component(
        base_image="python:3.12-slim",
        packages_to_install=["pandas", "pyarrow", "scikit-learn", "numpy"],
    )
    def evaluate_component(
        input_dataset: Input[Dataset],
        input_model: Input[Model],
        output_metrics: Output[Metrics],
    ) -> None:
        import pandas as pd
        import pickle
        from sklearn.metrics import accuracy_score, brier_score_loss

        df = pd.read_parquet(input_dataset.path)
        with open(input_model.path, "rb") as f:
            model_dict = pickle.load(f)

        model = model_dict["model"]
        feature_cols = model_dict["feature_cols"]
        avail = [c for c in feature_cols if c in df.columns]
        if not avail:
            return

        X = df[avail].fillna(0).values
        y_true = (df[avail[0]] > df[avail[0]].mean()).astype(int).values

        y_proba = model.predict_proba(X)[:, 1]
        output_metrics.log_metric("eval_accuracy", float(accuracy_score(y_true, (y_proba > 0.5).astype(int))))
        output_metrics.log_metric("eval_brier_score", float(brier_score_loss(y_true, y_proba)))

    @dsl.pipeline(
        name="spepe-ml-pipeline",
        description="SPEPE Electoral ML Pipeline: Feature Extract → Train → Evaluate → Promote",
        pipeline_root=PIPELINE_ROOT,
    )
    def spepe_pipeline(
        project_id: str = GCP_PROJECT,
        gold_dataset: str = "spepe_gold",
        gold_table: str = "fact_municipio_eleicao",
        n_bootstrap: int = 1000,
        target_candidate: str = "lula",
    ):
        features_task = extract_features_component(
            project_id=project_id,
            gold_dataset=gold_dataset,
            gold_table=gold_table,
        )
        train_task = train_bootstrap_component(
            input_dataset=features_task.outputs["output_dataset"],
            n_bootstrap=n_bootstrap,
            target_candidate=target_candidate,
        )
        evaluate_component(
            input_dataset=features_task.outputs["output_dataset"],
            input_model=train_task.outputs["output_model"],
        )

    return spepe_pipeline


def compile_pipeline(output_path: str = "output/ml_pipeline.yaml") -> str:
    """Compile pipeline to YAML for Vertex AI."""
    try:
        from kfp import compiler
    except ImportError:
        raise ImportError("kfp não instalado.")

    import os
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pipeline = build_pipeline()
    compiler.Compiler().compile(pipeline_func=pipeline, package_path=output_path)
    logger.info(f"Pipeline compilado: {output_path}")
    return output_path


def submit_pipeline(
    pipeline_yaml: str,
    parameters: dict | None = None,
    project_id: str | None = None,
    location: str | None = None,
    pipeline_root: str | None = None,
    job_display_name: str = "spepe-ml-run",
) -> str:
    """Submit compiled pipeline to Vertex AI."""
    project_id = project_id or GCP_PROJECT
    location = location or VERTEX_LOCATION
    pipeline_root = pipeline_root or PIPELINE_ROOT

    try:
        from google.cloud import aiplatform
        aiplatform.init(project=project_id, location=location)
        job = aiplatform.PipelineJob(
            display_name=job_display_name,
            template_path=pipeline_yaml,
            parameter_values=parameters or {},
            pipeline_root=pipeline_root,
            enable_caching=True,
        )
        job.submit()
        logger.info("Pipeline submetido: %s", job.resource_name)
        return job.resource_name
    except ImportError:
        raise ImportError("google-cloud-aiplatform não instalado.")


def run_hptuning_then_pipeline(
    project_id: str | None = None,
    location: str | None = None,
) -> str:
    """Run HP tuning first, then submit the pipeline with best params."""
    from mlops.components.hptuning import run_hptuning

    hp_result = run_hptuning(
        project_id=project_id or GCP_PROJECT,
        location=location or VERTEX_LOCATION,
    )
    logger.info(
        "HP tuning complete: best_params=%s brier=%.4f",
        hp_result.best_params, hp_result.best_metric_value,
    )

    pipeline_yaml = compile_pipeline("/tmp/spepe_pipeline_hp.yaml")
    return submit_pipeline(
        pipeline_yaml=pipeline_yaml,
        parameters={
            "n_bootstrap": int(hp_result.best_params.get("n_bootstrap", 1000)),
        },
        project_id=project_id,
        location=location,
        job_display_name=f"spepe-ml-hp-trial-{hp_result.trial_id}",
    )


if __name__ == "__main__":
    run_hptuning_then_pipeline()
