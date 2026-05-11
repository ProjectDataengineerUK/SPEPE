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

resource "google_cloud_scheduler_job" "social_rss_trends_daily" {
  name             = "spepe-social-rss-trends-daily-${var.environment}"
  description      = "Trigger social_ingest (RSS + Google Trends sources) daily at 23:00 BRT"
  schedule         = "0 2 * * *" # 02:00 UTC = 23:00 BRT (UTC-3)
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "600s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-social-ingest-${var.environment}:run"
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [{ name = "SOURCE_FILTER", value = "rss,google_trends" }] }] } }))
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "social_video_daily4x" {
  name             = "spepe-social-video-4xday-${var.environment}"
  description      = "Trigger social_ingest (YouTube + Facebook + Instagram) 4x/day at 00:00, 06:00, 12:00, 18:00 BRT"
  schedule         = "0 3,9,15,21 * * *" # 03/09/15/21 UTC = 00/06/12/18 BRT
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "900s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-social-ingest-${var.environment}:run"
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [{ name = "SOURCE_FILTER", value = "youtube,facebook,instagram" }] }] } }))
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "social_community_2xday" {
  name             = "spepe-social-community-2xday-${var.environment}"
  description      = "Trigger social_ingest (Bluesky + Reddit) 2x/day at 08:00 and 20:00 BRT"
  schedule         = "0 11,23 * * *" # 11/23 UTC = 08/20 BRT
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "600s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-social-ingest-${var.environment}:run"
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [{ name = "SOURCE_FILTER", value = "bluesky,reddit" }] }] } }))
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "social_twitter_daily" {
  name             = "spepe-social-twitter-daily-${var.environment}"
  description      = "Trigger social_ingest (Twitter/X) daily at 12:00 BRT — paused until budget approved"
  schedule         = "0 15 * * *" # 15:00 UTC = 12:00 BRT
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "600s"
  region           = var.region
  paused           = true # activate only when SOCIAL_X_ENABLED=true budget approved

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-social-ingest-${var.environment}:run"
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [{ name = "SOURCE_FILTER", value = "twitter" }, { name = "SOCIAL_X_ENABLED", value = "true" }] }] } }))
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "candidatos_discovery_monthly" {
  name             = "spepe-candidatos-discovery-monthly-${var.environment}"
  description      = "Trigger candidatos_discovery Cloud Run Job on the 1st of each month at 03:00 BRT"
  schedule         = "0 6 1 * *" # 06:00 UTC = 03:00 BRT, 1st of month
  time_zone        = "America/Sao_Paulo"
  attempt_deadline = "1800s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-candidatos-discovery-${var.environment}:run"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

# MLOps: monthly model retraining — 2h UTC on 1st (23h BRT previous day)
resource "google_cloud_scheduler_job" "ml_retrain_monthly" {
  name             = "spepe-ml-retrain-monthly-${var.environment}"
  description      = "Trigger gold_build Cloud Run Job on the 1st of each month at 02:00 UTC with RUN_ML_PIPELINE=true"
  schedule         = "0 2 1 * *"
  time_zone        = "UTC"
  attempt_deadline = "1800s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${local.run_api_base}/spepe-gold-build-${var.environment}:run"
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [{ name = "RUN_ML_PIPELINE", value = "true" }] }] } }))
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = local.scheduler_sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

# IAM binding moved to cloud_scheduler.tf (google_project_iam_member.scheduler_run_invoker)
