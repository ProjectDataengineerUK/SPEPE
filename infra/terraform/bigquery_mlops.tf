resource "google_bigquery_dataset" "spepe_mlops" {
  dataset_id  = "spepe_mlops"
  location    = var.region
  description = "SPEPE MLOps metadata: predictions, evaluations, bias metrics"
  labels      = local.labels

  access {
    role          = "WRITER"
    user_by_email = google_service_account.dataops_jobs.email
  }
  access {
    role          = "READER"
    user_by_email = google_service_account.cloud_run.email
  }
}

resource "google_bigquery_table" "fact_predictions" {
  dataset_id          = google_bigquery_dataset.spepe_mlops.dataset_id
  table_id            = "fact_predictions"
  description         = "All model predictions with IC 95% — joined with ground truth post-election"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type                     = "DAY"
    field                    = "prediction_date"
    require_partition_filter = true
  }

  clustering = ["candidato", "sg_uf", "model_version"]

  schema = jsonencode([
    { name = "prediction_id",   type = "STRING",    mode = "REQUIRED" },
    { name = "session_id",      type = "STRING",    mode = "REQUIRED" },
    { name = "candidato",       type = "STRING",    mode = "REQUIRED" },
    { name = "sg_uf",           type = "STRING",    mode = "NULLABLE" },
    { name = "prediction_date", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "p_mean",          type = "FLOAT64",   mode = "REQUIRED" },
    { name = "p_lower",         type = "FLOAT64",   mode = "REQUIRED" },
    { name = "p_upper",         type = "FLOAT64",   mode = "REQUIRED" },
    { name = "model_version",   type = "STRING",    mode = "REQUIRED" },
    { name = "features_hash",   type = "STRING",    mode = "NULLABLE" },
    { name = "actual_result",   type = "FLOAT64",   mode = "NULLABLE" },
    { name = "brier_score",     type = "FLOAT64",   mode = "NULLABLE" },
    { name = "evaluated_at",    type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "model_evaluations" {
  dataset_id          = google_bigquery_dataset.spepe_mlops.dataset_id
  table_id            = "model_evaluations"
  description         = "Backtest and canary evaluation results per model version"
  labels              = local.labels
  deletion_protection = false
  expiration_time     = timeadd(timestamp(), "17520h") # 2 years

  time_partitioning {
    type  = "DAY"
    field = "evaluated_at"
  }

  clustering = ["model_version", "eval_type"]

  schema = jsonencode([
    { name = "model_version",   type = "STRING",    mode = "REQUIRED" },
    { name = "evaluated_at",    type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "eval_type",       type = "STRING",    mode = "REQUIRED" },
    { name = "accuracy",        type = "FLOAT64",   mode = "NULLABLE" },
    { name = "brier_score",     type = "FLOAT64",   mode = "NULLABLE" },
    { name = "log_loss",        type = "FLOAT64",   mode = "NULLABLE" },
    { name = "mae",             type = "FLOAT64",   mode = "NULLABLE" },
    { name = "is_champion",     type = "BOOL",      mode = "REQUIRED" },
    { name = "hp_params",       type = "JSON",      mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "bias_metrics" {
  dataset_id          = google_bigquery_dataset.spepe_mlops.dataset_id
  table_id            = "bias_metrics"
  description         = "Fairness metrics per sg_uf, income quintile, and rural pct"
  labels              = local.labels
  deletion_protection = false
  expiration_time     = timeadd(timestamp(), "17520h") # 2 years

  time_partitioning {
    type  = "DAY"
    field = "measured_at"
  }

  clustering = ["model_version", "group_type"]

  schema = jsonencode([
    { name = "model_version",   type = "STRING",    mode = "REQUIRED" },
    { name = "measured_at",     type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "group_type",      type = "STRING",    mode = "REQUIRED" },
    { name = "group_value",     type = "STRING",    mode = "REQUIRED" },
    { name = "brier_score",     type = "FLOAT64",   mode = "REQUIRED" },
    { name = "global_brier",    type = "FLOAT64",   mode = "REQUIRED" },
    { name = "ratio",           type = "FLOAT64",   mode = "REQUIRED" },
    { name = "alert_triggered", type = "BOOL",      mode = "REQUIRED" },
    { name = "n_samples",       type = "INT64",     mode = "REQUIRED" },
  ])
}
