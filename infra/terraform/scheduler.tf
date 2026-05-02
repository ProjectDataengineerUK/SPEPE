locals {
  scheduler_sa_email = google_service_account.dataops_jobs.email
  run_api_base       = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs"
}

resource "google_cloud_scheduler_job" "pesquisas_ingest_weekly" {
  name             = "spepe-pesquisas-ingest-weekly-${var.environment}"
  description      = "Trigger pesquisas_ingest Cloud Run Job every Monday at 06:00 BRT"
  schedule         = "0 9 * * 1" # 09:00 UTC = 06:00 BRT (UTC-3)
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "1800s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-pesquisas-ingest-${var.environment}:run"
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "social_ingest_daily" {
  name             = "spepe-social-ingest-daily-${var.environment}"
  description      = "Trigger social_ingest Cloud Run Job daily at 03:00 BRT"
  schedule         = "0 6 * * *" # 06:00 UTC = 03:00 BRT
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "1800s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-social-ingest-${var.environment}:run"
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "reddit_ingest_daily" {
  name             = "spepe-reddit-ingest-daily-${var.environment}"
  description      = "Trigger reddit_ingest Cloud Run Job daily at 04:00 BRT"
  schedule         = "0 7 * * *" # 07:00 UTC = 04:00 BRT
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "1800s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-reddit-ingest-${var.environment}:run"
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "camara_senado_ingest_weekly" {
  name             = "spepe-camara-senado-ingest-weekly-${var.environment}"
  description      = "Trigger camara_senado_ingest Cloud Run Job every Wednesday at 05:00 BRT"
  schedule         = "0 8 * * 3" # 08:00 UTC = 05:00 BRT, Wednesday
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "1800s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-camara-senado-ingest-${var.environment}:run"
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_project_iam_member" "scheduler_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${local.scheduler_sa_email}"
}
