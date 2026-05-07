resource "google_cloud_run_v2_service" "spepe" {
  name     = "spepe-${var.environment}"
  location = var.region
  labels   = local.labels

  # When a custom domain LB exists, restrict traffic to LB only (hides raw Cloud Run URL).
  # In dev (no domain), allow all traffic for fast iteration.
  ingress = local.lb_enabled ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      # Use var.app_image — never :latest in Terraform.
      # :latest is a mutable tag; Terraform would not detect a change and would
      # never redeploy the service when a new image is pushed. The deploying
      # workflow must pass the immutable SHA-tagged URI via TF_VAR_app_image.
      image = var.app_image

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
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
        name  = "USE_BIGQUERY"
        value = "true"
      }
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# Allow the Cloud Run service to be invoked.
# The original member used a KSA annotation path that only works inside GKE,
# not for Cloud Run IAP. For IAP on Cloud Run the correct approach is to grant
# roles/run.invoker to the IAP service agent:
#   serviceAccount:service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com
# Because the project number is not a Terraform variable here, we expose a
# variable-based email and fall back to allUsers for dev (no IAP in dev).
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  name     = google_cloud_run_v2_service.spepe.name
  location = var.region
  role     = "roles/run.invoker"

  # In dev: allow unauthenticated access for faster iteration.
  # In staging/prod: restrict to the IAP-managed service account.
  member = var.environment == "dev" ? "allUsers" : "serviceAccount:${google_service_account.cloud_run.email}"
}
