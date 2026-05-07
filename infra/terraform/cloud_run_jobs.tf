locals {
  # dev: SP only; prod: all 27 UFs (--uf ALL triggers internal loop in each job)
  prod_mode = var.environment == "prod"
  uf_arg    = local.prod_mode ? "ALL" : "SP"

  jobs = {
    tse_ingest       = { timeout = local.prod_mode ? "7200s" : "3600s", memory = "8Gi",   cpu = "2", args = ["--uf", local.uf_arg] }
    ibge_sync        = { timeout = local.prod_mode ? "3600s" : "1800s", memory = "1Gi",   cpu = "1", args = ["--uf", local.uf_arg] }
    security_ingest  = { timeout = local.prod_mode ? "3600s" : "1800s", memory = "1Gi",   cpu = "1", args = ["--uf", local.uf_arg] }
    datasus_ingest   = { timeout = local.prod_mode ? "3600s" : "1800s", memory = "1Gi",   cpu = "1", args = ["--uf", local.uf_arg] }
    dieese_ingest    = { timeout = local.prod_mode ? "1800s" : "900s",  memory = "512Mi", cpu = "1", args = ["--uf", local.uf_arg] }
    cetic_ingest     = { timeout = local.prod_mode ? "1800s" : "900s",  memory = "512Mi", cpu = "1", args = ["--uf", local.uf_arg] }
    silver_transform = { timeout = local.prod_mode ? "7200s" : "1800s", memory = "8Gi",   cpu = "4", args = ["--uf", local.uf_arg] }
    gold_build       = { timeout = "1800s", memory = "2Gi", cpu = "2", args = [] }
    digital_ingest   = { timeout = "900s",  memory = "1Gi", cpu = "1", args = [] }
    social_ingest         = { timeout = "1800s", memory = "1Gi",   cpu = "1", args = [] }
    pesquisas_ingest      = { timeout = "1800s", memory = "1Gi",   cpu = "1", args = [] }
    tse_perfil_ingest     = { timeout = local.prod_mode ? "3600s" : "1800s", memory = "2Gi", cpu = "1", args = ["--uf", local.uf_arg] }
    tse_candidaturas_ingest = { timeout = local.prod_mode ? "3600s" : "1800s", memory = "1Gi", cpu = "1", args = ["--uf", local.uf_arg] }
    reddit_ingest         = { timeout = "1800s", memory = "512Mi", cpu = "1", args = [] }
    camara_senado_ingest  = { timeout = "3600s", memory = "1Gi",   cpu = "1", args = [] }
    endividamento_ingest  = { timeout = local.prod_mode ? "1800s" : "900s", memory = "512Mi", cpu = "1", args = ["--uf", local.uf_arg] }
    cadunico_ingest       = { timeout = "3600s", memory = "512Mi", cpu = "1", args = [] }
    emendas_ingest        = { timeout = "3600s", memory = "512Mi", cpu = "1", args = [] }
    sancoes_ingest        = { timeout = "3600s", memory = "512Mi", cpu = "1", args = [] }
  }

  # Env vars adicionais por job (além das compartilhadas)
  job_extra_env = {
    tse_ingest       = {}
    ibge_sync        = {}
    security_ingest  = {}
    datasus_ingest   = {}
    dieese_ingest    = { DEFAULT_ANO = "2025" }
    cetic_ingest     = {}
    silver_transform = { SOCIAL_YEAR = "2026" }
    gold_build       = {}
    digital_ingest   = {}
    pesquisas_ingest = {
      PESQUISAS_YEAR       = "2026"
      PESQUISAS_CARGOS     = "1 3"
      PESQUISAS_ENRICH_PDF = "false"
    }
    social_ingest = {
      SOCIAL_CANDIDATOS = jsonencode([
        "Lula", "Lula da Silva",
        "Tarcísio de Freitas", "Tarcísio Freitas",
        "Bolsonaro", "Jair Bolsonaro",
        "Simone Tebet", "Ciro Gomes",
        "Alckmin", "Geraldo Alckmin",
        "Rodrigo Pacheco",
        "Fernando Haddad", "Guilherme Boulos",
      ])
      SOCIAL_DIAS = "7"
      SOCIAL_YEAR = "2026"
    }
    tse_perfil_ingest     = {}
    tse_candidaturas_ingest = {}
    reddit_ingest = {
      REDDIT_SUBREDDITS = jsonencode(["brasil", "politica", "brasilivre"])
      REDDIT_DIAS       = "30"
    }
    camara_senado_ingest = { LEGISLATURE = "57" }
    endividamento_ingest = {
      ENDIVIDAMENTO_YEAR_START = "2025"
      ENDIVIDAMENTO_YEAR_END   = "2026"
    }
    cadunico_ingest = { CADUNICO_YEAR = "2018,2022,2024,2025" }
    emendas_ingest  = { EMENDAS_YEAR = "2018,2022,2025" }
    sancoes_ingest  = { SANCOES_YEAR = "2026" }
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
    social_ingest           = ["python", "-m", "dataops.jobs.social_ingest_job"]
    pesquisas_ingest        = ["python", "-m", "dataops.jobs.pesquisas_ingest_job"]
    tse_perfil_ingest       = ["python", "-m", "dataops.jobs.tse_perfil_ingest_job"]
    tse_candidaturas_ingest = ["python", "-m", "dataops.jobs.tse_candidaturas_ingest_job"]
    reddit_ingest           = ["python", "-m", "dataops.jobs.reddit_ingest_job"]
    camara_senado_ingest    = ["python", "-m", "dataops.jobs.camara_senado_ingest_job"]
    endividamento_ingest    = ["python", "-m", "dataops.jobs.endividamento_ingest_job"]
    cadunico_ingest         = ["python", "-m", "dataops.jobs.cadunico_ingest_job"]
    emendas_ingest          = ["python", "-m", "dataops.jobs.emendas_ingest_job"]
    sancoes_ingest          = ["python", "-m", "dataops.jobs.sancoes_ingest_job"]
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
        env {
          name = "X_BEARER_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.x_bearer_token.secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "TRANSPARENCIA_API_KEY"
          value_source {
            secret_key_ref {
              secret  = "TRANSPARENCIA_API_KEY"
              version = "latest"
            }
          }
        }

        dynamic "env" {
          for_each = local.job_extra_env[each.key]
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.dataops_meta,
    google_secret_manager_secret_iam_member.dataops_youtube,
    google_secret_manager_secret_iam_member.dataops_x_bearer,
    google_secret_manager_secret_iam_member.dataops_transparencia,
  ]
}

output "cloud_run_job_names" {
  value       = { for k, v in google_cloud_run_v2_job.spepe_jobs : k => v.name }
  description = "Cloud Run Job names keyed by job type"
}
