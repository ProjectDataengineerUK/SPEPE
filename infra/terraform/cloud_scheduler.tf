# Cloud Scheduler — disparo automático dos Cloud Run Jobs de ingestão
#
# Estratégia de frequência (horários em UTC = BRT+3):
#   Diário    → social/digital/reddit/pesquisas (dados mudam todo dia)
#   Semanal   → camara/security/sancoes/candidatos (mudam semanalmente)
#   Mensal    → TSE/IBGE/DATASUS/DIEESE/CETIC/etc. (ciclo de publicação mensal)
#   Pipeline  → silver (diário 10:00 UTC, após ingestões overnight)
#               gold   (diário 12:00 UTC, após silver)
#
# Em dev: schedulers criados mas pausados (não disparam automaticamente).
# Em prod: schedulers ativos.

locals {
  scheduler_paused = var.environment != "prod"

  # URL base da API Cloud Run Jobs v2 para trigger via HTTP
  scheduler_run_base = "https://${var.region}-run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs"

  # Mapa job_key → { schedule, description }
  # schedule usa cron padrão no fuso America/Sao_Paulo
  schedules = {
    # ── Diário (ingestão de dados que mudam todo dia) ─────────────────────────
    social_ingest    = { schedule = "0 2 * * *",  desc = "Social media — diário 02:00 BRT" }
    digital_ingest   = { schedule = "30 2 * * *", desc = "Google Trends + Meta — diário 02:30 BRT" }
    reddit_ingest    = { schedule = "0 3 * * *",  desc = "Reddit Brasil — diário 03:00 BRT" }
    pesquisas_ingest = { schedule = "30 3 * * *", desc = "Pesquisas eleitorais — diário 03:30 BRT" }

    # ── Pipeline diário (após ingestões overnight) ────────────────────────────
    silver_transform = { schedule = "0 7 * * *",  desc = "Silver transform — diário 07:00 BRT (após ingestões)" }
    gold_build       = { schedule = "0 9 * * *",  desc = "Gold build — diário 09:00 BRT (após silver)" }

    # ── Semanal (segunda-feira) ───────────────────────────────────────────────
    camara_senado_ingest = { schedule = "0 4 * * 1",  desc = "Câmara/Senado — semanal segunda 04:00 BRT" }
    security_ingest      = { schedule = "30 4 * * 1", desc = "Segurança pública — semanal segunda 04:30 BRT" }
    sancoes_ingest       = { schedule = "0 5 * * 1",  desc = "CEIS/CNEP sanções — semanal segunda 05:00 BRT" }
    candidatos_discovery = { schedule = "30 5 * * 1", desc = "Discovery candidatos — semanal segunda 05:30 BRT" }

    # ── Mensal (dia 1 de cada mês) ────────────────────────────────────────────
    tse_ingest              = { schedule = "0 6 1 * *",  desc = "TSE resultados — mensal dia 1 06:00 BRT" }
    tse_perfil_ingest       = { schedule = "0 7 1 * *",  desc = "TSE perfil eleitorado — mensal dia 1 07:00 BRT" }
    tse_candidaturas_ingest = { schedule = "0 8 1 * *",  desc = "TSE candidaturas — mensal dia 1 08:00 BRT" }
    ibge_sync               = { schedule = "0 9 1 * *",  desc = "IBGE SIDRA — mensal dia 1 09:00 BRT" }
    datasus_ingest          = { schedule = "0 10 1 * *", desc = "DATASUS — mensal dia 1 10:00 BRT" }
    dieese_ingest           = { schedule = "0 11 1 * *", desc = "DIEESE — mensal dia 1 11:00 BRT" }
    cetic_ingest            = { schedule = "0 12 1 * *", desc = "CETIC.br — mensal dia 1 12:00 BRT" }
    endividamento_ingest    = { schedule = "0 13 1 * *", desc = "Endividamento BACEN — mensal dia 1 13:00 BRT" }
    cadunico_ingest         = { schedule = "0 14 1 * *", desc = "CadÚnico/BF — mensal dia 1 14:00 BRT" }
    emendas_ingest          = { schedule = "0 15 1 * *", desc = "Emendas parlamentares — mensal dia 1 15:00 BRT" }
  }
}

# SA dedicado para o Scheduler (princípio de menor privilégio)
resource "google_service_account" "scheduler" {
  account_id   = "spepe-scheduler"
  display_name = "SPEPE Cloud Scheduler — trigger Cloud Run Jobs"
  project      = var.project_id
}

# roles/run.invoker no projeto — permite executar qualquer Cloud Run Job
resource "google_project_iam_member" "scheduler_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

# Um scheduler por job, via for_each
resource "google_cloud_scheduler_job" "spepe_jobs" {
  for_each = local.schedules

  name        = "spepe-schedule-${replace(each.key, "_", "-")}"
  description = each.value.desc
  schedule    = each.value.schedule
  time_zone   = "America/Sao_Paulo"
  region      = var.region
  project     = var.project_id

  # Pausado em dev — ativo apenas em prod
  paused = local.scheduler_paused

  # Dispara a API Cloud Run Jobs v2 diretamente via HTTP POST
  http_target {
    http_method = "POST"
    uri         = "${local.scheduler_run_base}/spepe-${replace(each.key, "_", "-")}:run"
    body        = base64encode("{}")

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      # audience = URI sem path (exigido pelo Cloud Run para validar o token)
      audience = local.scheduler_run_base
    }
  }

  # Retry: até 3 tentativas com backoff exponencial (padrão GCP)
  retry_config {
    retry_count          = 3
    min_backoff_duration = "5s"
    max_backoff_duration = "600s"
    max_doublings        = 3
  }

  depends_on = [
    google_service_account.scheduler,
    google_project_iam_member.scheduler_run_invoker,
  ]
}
