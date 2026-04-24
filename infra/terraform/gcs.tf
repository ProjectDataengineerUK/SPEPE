resource "google_storage_bucket" "spepe_data" {
  name          = "${var.project_id}-spepe-data"
  location      = var.region
  force_destroy = var.environment != "prod"

  labels = local.labels

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 365
      with_state = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  cors {
    origin          = ["*"]
    method          = ["GET"]
    response_header = ["Content-Type"]
    max_age_seconds = 3600
  }
}

resource "google_storage_bucket_iam_member" "cloud_run_reader" {
  bucket = google_storage_bucket.spepe_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_storage_bucket_iam_member" "dataops_writer" {
  bucket = google_storage_bucket.spepe_data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataops_jobs.email}"
}

resource "google_service_account" "cloud_run" {
  account_id   = "spepe-cloud-run"
  display_name = "SPEPE Cloud Run Service Account"
}

resource "google_service_account" "dataops_jobs" {
  account_id   = "spepe-dataops-jobs"
  display_name = "SPEPE DataOps Jobs Service Account"
}
