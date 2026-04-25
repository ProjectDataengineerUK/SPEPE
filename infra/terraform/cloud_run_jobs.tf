locals {
  jobs = {
    tse_ingest       = { timeout = "3600s", memory = "2Gi", cpu = "2", args = ["--uf", "SP"] }
    ibge_sync        = { timeout = "1800s", memory = "1Gi", cpu = "1", args = ["--uf", "SP"] }
    security_ingest  = { timeout = "1800s", memory = "1Gi", cpu = "1", args = ["--uf", "SP"] }
    datasus_ingest   = { timeout = "1800s", memory = "1Gi", cpu = "1", args = ["--uf", "SP"] }
    dieese_ingest    = { timeout = "900s",  memory = "512Mi", cpu = "1", args = ["--uf", "SP"] }
    cetic_ingest     = { timeout = "900s",  memory = "512Mi", cpu = "1", args = ["--uf", "SP"] }
    silver_transform = { timeout = "1800s", memory = "2Gi", cpu = "2", args = ["--uf", "SP"] }
    gold_build       = { timeout = "1800s", memory = "2Gi", cpu = "2", args = [] }
    digital_ingest   = { timeout = "900s", memory = "1Gi", cpu = "1", args = [] }
  }

  job_entrypoints = {
    tse_ingest       = ["python", "-m", "dataops.jobs.tse_ingest_job"]
    ibge_sync        = ["python", "-m", "dataops.jobs.ibge_sync_job"]
    security_ingest  = ["python", "-m", "dataops.jobs.security_ingest_job"]
    datasus_ingest   = ["python", "-m", "dataops.jobs.datasus_ingest_job"]
    dieese_ingest    = ["python", "-m", "dataops.jobs.dieese_ingest_job"]
    cetic_ingest     = ["python", "-m", "dataops.jobs.cetic_ingest_job"]
    silver_transform = ["python", "-m", "dataops.jobs.silver_transform_job"]
    gold_build       = ["python", "-m", "dataops.jobs.gold_build_job"]
    digital_ingest   = ["python", "-m", "dataops.jobs.digital_ingest_job"]
  }
}

resource "google_cloud_run_v2_job" "spepe_jobs" {
  for_each = local.jobs

  name     = "spepe-${replace(each.key, "_", "-")}"
  location = var.region
  labels   = local.labels

  template {
    template {
      service_account = google_service_account.dataops_jobs.email
      timeout         = each.value.timeout

      containers {
        image   = var.app_image
        command = local.job_entrypoints[each.key]
        args    = each.value.args

        resources {
          limits = {
            cpu    = each.value.cpu
            memory = each.value.memory
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.spepe_data.name
        }
        env {
          name  = "BIGQUERY_DATASET_SILVER"
          value = google_bigquery_dataset.spepe_silver.dataset_id
        }
        env {
          name  = "BIGQUERY_DATASET_GOLD"
          value = google_bigquery_dataset.spepe_gold.dataset_id
        }
        env {
          name = "META_APP_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.meta_app_token.secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "YOUTUBE_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.youtube_api_key.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.dataops_meta,
    google_secret_manager_secret_iam_member.dataops_youtube,
  ]
}

output "cloud_run_job_names" {
  value       = { for k, v in google_cloud_run_v2_job.spepe_jobs : k => v.name }
  description = "Cloud Run Job names keyed by job type"
}
