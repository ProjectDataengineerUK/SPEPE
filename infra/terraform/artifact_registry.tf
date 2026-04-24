resource "google_artifact_registry_repository" "spepe" {
  location      = var.region
  repository_id = "spepe"
  format        = "DOCKER"
  description   = "SPEPE container images"
  labels        = local.labels
}

# Allow Cloud Run SA to pull images
resource "google_artifact_registry_repository_iam_member" "cloud_run_reader" {
  location   = google_artifact_registry_repository.spepe.location
  repository = google_artifact_registry_repository.spepe.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.cloud_run.email}"
}

# Allow GitHub Actions SA (WIF) to push images
resource "google_artifact_registry_repository_iam_member" "github_writer" {
  location   = google_artifact_registry_repository.spepe.location
  repository = google_artifact_registry_repository.spepe.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_actions.email}"
}

output "artifact_registry_url" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.spepe.repository_id}"
  description = "Artifact Registry base URL for image pushes"
}
