# Sentinel — Always-on monitoring orchestrator (min_instances=1, low CPU)

locals {
  sentinel_name = "spepe-sentinel-${var.environment}"
}

resource "google_cloud_run_v2_service" "sentinel" {
  name     = local.sentinel_name
  location = var.region
  labels   = local.labels

  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.cloud_run.email

    scaling {
      min_instance_count = 1
      max_instance_count = 2
    }

    containers {
      image = var.app_image

      command = ["python", "-m", "sentinel.main"]

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = false
        startup_cpu_boost = false
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "SENTINEL_ENABLED"
        value = "true"
      }
      env {
        name  = "SENTINEL_KB_COLLECTION"
        value = "sentinel_kb"
      }
      env {
        name  = "SENTINEL_ALERT_TOPIC"
        value = google_pubsub_topic.sentinel_alerts.name
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

  depends_on = [
    google_pubsub_topic.sentinel_events,
    google_pubsub_topic.sentinel_alerts,
  ]
}

# IAM: allow Pub/Sub to invoke Sentinel via push subscription
resource "google_cloud_run_v2_service_iam_member" "sentinel_pubsub_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.sentinel.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cloud_run.email}"
}

# Firestore collection for sentinel KB (Firestore is provisioned in firestore.tf)
# sentinel_kb collection is created at runtime by knowledge_base.py — no resource needed.
