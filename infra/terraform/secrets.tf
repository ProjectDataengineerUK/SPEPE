resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "ANTHROPIC_API_KEY"
  replication { auto {} }
  labels = local.labels
}

resource "google_secret_manager_secret" "meta_app_token" {
  secret_id = "META_APP_TOKEN"
  replication { auto {} }
  labels = local.labels
}

resource "google_secret_manager_secret" "youtube_api_key" {
  secret_id = "YOUTUBE_API_KEY"
  replication { auto {} }
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
