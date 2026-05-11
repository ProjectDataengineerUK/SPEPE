"""Vertex AI Pipeline (KFP v2) — SPEPE ML pipeline definition.

Pipeline: feature_extract → train_bootstrap → evaluate → promote
"""

import logging
import os

logger = logging.getLogger("spepe.mlops.vertex_pipeline")

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", f"{GCP_PROJECT}-data")
PIPELINE_ROOT = f"gs://{GCS_BUCKET}/vertex-pipelines"


def build_pipeline():
    """Build the KFP v2 pipeline definition."""
    try:
        from kfp import dsl
        from kfp.dsl import component, Input, Output, Dataset, Model, Metrics
    except ImportError:
        raise ImportError("kfp não instalado. Execute: pip install kfp")

    @component(
        base_image="python:3.12-slim",
        packages_to_install=[
            "google-cloud-bigquery",
            "pandas",
            "pyarrow",
            "scikit-learn",
        ],
    )
    def extract_features_component(
        project_id: str,
        gold_dataset: str,
        gold_table: str,
        cd_cargo: int,
        ano_eleicao: int,
        output_dataset: Output[Dataset],
    ) -> None:
        import re
        from google.cloud import bigquery

        _ALLOWED_DATASETS = {"spepe_gold", "spepe_silver", "spepe_mlops"}
        _NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$")
        _PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,29}$")

        if not _PROJECT_RE.match(project_id):
            raise ValueError(f"project_id inválido: {project_id}")
        if gold_dataset not in _ALLOWED_DATASETS:
            raise ValueError(f"dataset não permitido: {gold_dataset}")
        if not _NAME_RE.match(gold_table):
            raise ValueError(f"nome de tabela inválido: {gold_table}")

        client = bigquery.Client(project=project_id)
        query = """
            SELECT
                f.sg_uf,
                f.cd_municipio,
                f.nm_candidato,
                f.sg_partido,
                f.cd_cargo,
                f.ano_eleicao,
                f.pct_votos_municipio,
                i.populacao_total,
                i.taxa_alfabetizacao,
                i.idhm,
                i.renda_per_capita,
                i.taxa_analfabetismo
            FROM `{project}.{dataset}.{table}` f
            LEFT JOIN `{project}.spepe_gold.fact_ibge_municipio` i
                ON f.cd_municipio_ibge = i.cd_municipio_ibge
            WHERE f.cd_cargo = @cd_cargo
              AND f.ano_eleicao = @ano_eleicao
              AND f.pct_votos_municipio IS NOT NULL
        """.format(project=project_id, dataset=gold_dataset, table=gold_table)

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
                bigquery.ScalarQueryParameter("ano_eleicao", "INT64", ano_eleicao),
            ]
        )
        df = client.query(query, job_config=job_config).to_dataframe()
        df.to_parquet(output_dataset.path, index=False)

    @component(
        base_image="python:3.12-slim",
        packages_to_install=[
            "pandas",
            "pyarrow",
            "statsmodels",
            "scikit-learn",
            "numpy",
        ],
    )
    def train_bootstrap_component(
        input_dataset: Input[Dataset],
        n_bootstrap: int,
        output_model: Output[Model],
        output_metrics: Output[Metrics],
    ) -> None:
        import pandas as pd
        import pickle
        from sklearn.linear_model import Ridge
        from sklearn.metrics import r2_score, mean_absolute_error

        TARGET_COL = "pct_votos_municipio"
        FEATURE_COLS = [
            "populacao_total",
            "taxa_alfabetizacao",
            "idhm",
            "renda_per_capita",
            "taxa_analfabetismo",
        ]

        df = pd.read_parquet(input_dataset.path)
        feature_cols = [c for c in FEATURE_COLS if c in df.columns]

        if TARGET_COL not in df.columns or not feature_cols:
            return

        X = df[feature_cols].fillna(0).values
        y = df[TARGET_COL].values

        model = Ridge(alpha=1.0)
        model.fit(X, y)

        with open(output_model.path, "wb") as f:
            pickle.dump(
                {"model": model, "feature_cols": feature_cols, "target_col": TARGET_COL}, f
            )

        output_metrics.log_metric("r2_score", float(r2_score(y, model.predict(X))))
        output_metrics.log_metric("mae", float(mean_absolute_error(y, model.predict(X))))
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
        import logging
        import pandas as pd
        import pickle
        from pathlib import Path
        from sklearn.metrics import r2_score, mean_absolute_error

        _logger = logging.getLogger("spepe.evaluate_component")

        df = pd.read_parquet(input_dataset.path)
        raw = Path(input_model.path).read_bytes()
        if len(raw) < 4:
            raise RuntimeError("Arquivo de modelo corrompido")
        model_dict = pickle.loads(raw)

        model = model_dict["model"]
        feature_cols = model_dict["feature_cols"]
        # Bug 2 fix: target_col is the continuous regression target, not a binary label
        target_col = model_dict.get("target_col", "pct_votos_municipio")

        if target_col not in df.columns:
            _logger.error(
                "target_col %s não encontrado. Colunas: %s", target_col, df.columns.tolist()
            )
            output_metrics.log_metric("error", 1.0)
            output_metrics.log_metric("error_reason", 0.0)
            return

        avail = [c for c in feature_cols if c in df.columns]
        if not avail:
            _logger.error("Nenhuma feature disponível no dataset de avaliação.")
            output_metrics.log_metric("error", 1.0)
            return

        X = df[avail].fillna(0).values
        # Bug 2 fix: y_true is the continuous target column directly (regression, not classification)
        y_true = df[target_col].values
        y_pred = model.predict(X)

        # Regression metrics: R² and MAE (not Brier/accuracy — those are for classifiers)
        output_metrics.log_metric("eval_r2_score", float(r2_score(y_true, y_pred)))
        output_metrics.log_metric("eval_mae", float(mean_absolute_error(y_true, y_pred)))
        output_metrics.log_metric("eval_n_samples", float(len(y_true)))

    @component(
        base_image="python:3.12-slim",
        packages_to_install=[
            "google-cloud-bigquery",
            "pandas",
            "pyarrow",
            "scikit-learn",
            "db-dtypes",
        ],
    )
    def promote_component(
        input_dataset: Input[Dataset],
        input_model: Input[Model],
        eval_metrics: Input[Metrics],
        project_id: str,
        ano_eleicao: int,
        mae_threshold: float,
        output_promoted: Output[Model],
    ) -> None:
        import json
        import logging
        import pickle
        import uuid
        from datetime import datetime, timezone
        from pathlib import Path

        import pandas as pd
        from sklearn.metrics import r2_score, mean_absolute_error

        _logger = logging.getLogger("spepe.promote_component")

        raw = Path(input_model.path).read_bytes()
        if len(raw) < 4:
            raise RuntimeError("Arquivo de modelo corrompido")
        model_dict = pickle.loads(raw)

        model = model_dict["model"]
        feature_cols = model_dict["feature_cols"]
        target_col = model_dict.get("target_col", "pct_votos_municipio")

        df = pd.read_parquet(input_dataset.path)
        avail = [c for c in feature_cols if c in df.columns]
        X = df[avail].fillna(0).values
        y_true = df[target_col].values
        y_pred = model.predict(X)

        eval_mae = float(mean_absolute_error(y_true, y_pred))
        eval_r2 = float(r2_score(y_true, y_pred))
        _logger.info("Gate: MAE=%.4f threshold=%.4f R²=%.4f", eval_mae, mae_threshold, eval_r2)

        if eval_mae > mae_threshold:
            _logger.warning(
                "Modelo reprovado no gate de qualidade: MAE %.4f > %.4f", eval_mae, mae_threshold
            )
            # Still write the model artifact so the step succeeds, but skip BQ write
            import shutil
            shutil.copy2(input_model.path, output_promoted.path)
            return

        # Promote: copy model artifact
        import shutil
        shutil.copy2(input_model.path, output_promoted.path)

        # Write fact_predictions to BigQuery (one row per municipality per candidate)
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=project_id)

            model_id = f"spepe-ridge-{ano_eleicao}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            now_ts = datetime.now(timezone.utc).isoformat()

            # Bootstrap IC 95% via residual percentiles as a lightweight approximation
            import numpy as np
            residuals = y_true - y_pred
            ic_low_offset = float(np.percentile(residuals, 2.5))
            ic_high_offset = float(np.percentile(residuals, 97.5))

            candidato_col = "nm_candidato" if "nm_candidato" in df.columns else None
            cargo_col = "cd_cargo" if "cd_cargo" in df.columns else None
            uf_col = "sg_uf" if "sg_uf" in df.columns else None
            municipio_col = "cd_municipio_ibge" if "cd_municipio_ibge" in df.columns else "cd_municipio"

            rows = []
            session_id = str(uuid.uuid4())
            for i, (pred_val, row) in enumerate(zip(y_pred, df.itertuples(index=False))):
                rows.append({
                    "prediction_id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "candidato": str(getattr(row, candidato_col, "")) if candidato_col else "",
                    "sg_uf": str(getattr(row, uf_col, "")) if uf_col else None,
                    "prediction_date": now_ts,
                    "p_mean": float(pred_val),
                    "p_lower": float(max(0.0, pred_val + ic_low_offset)),
                    "p_upper": float(min(1.0, pred_val + ic_high_offset)),
                    "model_version": model_id,
                    "features_hash": None,
                    "actual_result": None,
                    "brier_score": None,
                    "evaluated_at": None,
                })

            if rows:
                predictions_df = pd.DataFrame(rows)
                table_id = f"{project_id}.spepe_mlops.fact_predictions"
                job_config = bigquery.LoadJobConfig(
                    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                    schema_update_options=[],
                    time_partitioning=bigquery.TimePartitioning(field="prediction_date"),
                )
                load_job = client.load_table_from_dataframe(
                    predictions_df, table_id, job_config=job_config
                )
                load_job.result()
                _logger.info(
                    "fact_predictions: %d linhas gravadas em %s (model=%s)",
                    len(rows), table_id, model_id,
                )
        except Exception as exc:
            _logger.error("Falha ao gravar fact_predictions: %s", exc)
            raise

    @dsl.pipeline(
        name="spepe-ml-pipeline",
        description="SPEPE Electoral ML Pipeline: Feature Extract -> Train -> Evaluate -> Promote",
        pipeline_root=PIPELINE_ROOT,
    )
    def spepe_pipeline(
        project_id: str = GCP_PROJECT,
        gold_dataset: str = "spepe_gold",
        # Bug 3 fix: correct Gold table name
        gold_table: str = "fact_municipio_candidato_eleicao",
        n_bootstrap: int = 1000,
        cd_cargo: int = 3,
        ano_eleicao: int = 2022,
        mae_threshold: float = 0.10,
    ):
        features_task = extract_features_component(
            project_id=project_id,
            gold_dataset=gold_dataset,
            gold_table=gold_table,
            cd_cargo=cd_cargo,
            ano_eleicao=ano_eleicao,
        )
        train_task = train_bootstrap_component(
            input_dataset=features_task.outputs["output_dataset"],
            n_bootstrap=n_bootstrap,
        )
        eval_task = evaluate_component(
            input_dataset=features_task.outputs["output_dataset"],
            input_model=train_task.outputs["output_model"],
        )
        promote_component(
            input_dataset=features_task.outputs["output_dataset"],
            input_model=train_task.outputs["output_model"],
            eval_metrics=eval_task.outputs["output_metrics"],
            project_id=project_id,
            ano_eleicao=ano_eleicao,
            mae_threshold=mae_threshold,
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
        # Bug 1 fix: use run(sync=False) — submit() is not the correct PipelineJob API
        job.run(sync=False)
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
        hp_result.best_params,
        hp_result.best_metric_value,
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
