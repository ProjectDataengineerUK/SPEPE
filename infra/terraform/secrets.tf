resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "ANTHROPIC_API_KEY"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret" "meta_app_token" {
  secret_id = "META_APP_TOKEN"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret" "youtube_api_key" {
  secret_id = "YOUTUBE_API_KEY"
  replication {
    auto {}
  }
  labels = local.labels
}

# Grant Cloud Run SA access to all secrets
resource "google_secret_manager_secret_iam_member" "cloud_run_anthropic" {
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_meta" {
  secret_id = google_secret_manager_secret.meta_app_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_youtube" {
  secret_id = google_secret_manager_secret.youtube_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

# Bootstrap placeholder versions — overwrite with real values via Secret Manager console or CLI:
#   gcloud secrets versions add ANTHROPIC_API_KEY --data-file=- <<< "sk-ant-real-key"
resource "google_secret_manager_secret_version" "anthropic_api_key_placeholder" {
  secret      = google_secret_manager_secret.anthropic_api_key.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

#
#   gcloud secrets versions add META_APP_TOKEN --data-file=-  <<< "real-token"
#   gcloud secrets versions add YOUTUBE_API_KEY --data-file=- <<< "real-key"
resource "google_secret_manager_secret_version" "meta_app_token_placeholder" {
  secret      = google_secret_manager_secret.meta_app_token.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret_version" "youtube_api_key_placeholder" {
  secret      = google_secret_manager_secret.youtube_api_key.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

# Grant DataOps Jobs SA access to secrets needed by digital_ingest job
resource "google_secret_manager_secret_iam_member" "dataops_meta" {
  secret_id = google_secret_manager_secret.meta_app_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_secret_manager_secret_iam_member" "dataops_youtube" {
  secret_id = google_secret_manager_secret.youtube_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

# ── X (Twitter) Bearer Token — Fase 2 Social ─────────────────────────────
resource "google_secret_manager_secret" "x_bearer_token" {
  secret_id = "X_BEARER_TOKEN"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret_version" "x_bearer_token_placeholder" {
  secret      = google_secret_manager_secret.x_bearer_token.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret_iam_member" "cloud_run_x_bearer" {
  secret_id = google_secret_manager_secret.x_bearer_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "dataops_x_bearer" {
  secret_id = google_secret_manager_secret.x_bearer_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_secret_manager_secret" "reddit_client_id" {
  secret_id = "REDDIT_CLIENT_ID"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret" "reddit_client_secret" {
  secret_id = "REDDIT_CLIENT_SECRET"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret_version" "reddit_client_id_placeholder" {
  secret      = google_secret_manager_secret.reddit_client_id.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret_version" "reddit_client_secret_placeholder" {
  secret      = google_secret_manager_secret.reddit_client_secret.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret_iam_member" "dataops_reddit_id" {
  secret_id = google_secret_manager_secret.reddit_client_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_secret_manager_secret_iam_member" "dataops_reddit_secret" {
  secret_id = google_secret_manager_secret.reddit_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

# ── Google Maps API Key ───────────────────────────────────────────────────────
resource "google_secret_manager_secret" "google_maps_api_key" {
  secret_id = "GOOGLE_MAPS_API_KEY"
  project   = var.project_id
  replication { auto {} }
  labels = local.labels
}

resource "google_secret_manager_secret_version" "google_maps_api_key_placeholder" {
  secret      = google_secret_manager_secret.google_maps_api_key.id
  secret_data = "placeholder"
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret_iam_member" "cloud_run_google_maps" {
  secret_id = google_secret_manager_secret.google_maps_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ── Portal Transparência API Key — CadÚnico, Emendas, Sanções ────────────────
# Secret created out-of-band; managed here for IAM only.
data "google_secret_manager_secret" "transparencia_api_key" {
  secret_id = "TRANSPARENCIA_API_KEY"
  project   = var.project_id
}

resource "google_secret_manager_secret_iam_member" "dataops_transparencia" {
  secret_id = data.google_secret_manager_secret.transparencia_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataops_jobs.email}"
}
