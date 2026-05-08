data "google_project" "spepe" {
  project_id = var.project_id
}

# ── Service Account — GitHub Actions (cloud_run e dataops_jobs já estão em gcs.tf) ──

resource "google_service_account" "github_actions" {
  account_id   = "spepe-github-actions"
  display_name = "SPEPE GitHub Actions CI/CD"
  project      = var.project_id
}

# ── IAM — Cloud Run SA ────────────────────────────────────────────────────────

resource "google_project_iam_member" "cloud_run_bigquery" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_gcs" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ── IAM — DataOps Jobs SA ─────────────────────────────────────────────────────

resource "google_project_iam_member" "dataops_bigquery" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_project_iam_member" "dataops_gcs" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_project_iam_member" "dataops_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_project_iam_member" "dataops_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.dataops_jobs.email}"
}


# ── IAM — GitHub Actions SA ───────────────────────────────────────────────────

resource "google_project_iam_member" "github_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_project_iam_member" "github_storage" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_project_iam_member" "github_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

# ── WIF → impersonate GitHub Actions SA ──────────────────────────────────────

resource "google_service_account_iam_member" "wif_github_impersonate" {
  service_account_id = google_service_account.github_actions.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# Allow Pub/Sub service agent to create OIDC tokens for cloud_run SA
# Required for push subscriptions that deliver to authenticated Cloud Run endpoints
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.cloud_run.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.spepe.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "github_actions_sa" {
  value       = google_service_account.github_actions.email
  description = "GitHub Actions service account — add to WIF binding"
}
