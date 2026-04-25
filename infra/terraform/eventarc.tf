# Pub/Sub → Cloud Run: triggered when drift monitor publishes to drift-detected topic
# Uses transport.pubsub (correct for Pub/Sub events) instead of matching_criteria resourceName
resource "google_eventarc_trigger" "drift_retrain" {
  name     = "spepe-drift-retrain-trigger"
  location = var.region
  labels   = local.labels

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = google_pubsub_topic.drift_detected.id
    }
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.spepe.name
      region  = var.region
      path    = "/jobs/retrain-trigger"
    }
  }

  service_account = google_service_account.cloud_run.email

  depends_on = [
    google_pubsub_topic.drift_detected,
    google_cloud_run_v2_service.spepe,
  ]
}

# Vertex AI → Cloud Run canary: activated when a new model version is uploaded
# Only relevant in Phase 3 (MLOps). Disabled in Phase 1 (dev).
# Re-enable and update event type when Vertex AI model registry is in use.
# resource "google_eventarc_trigger" "model_canary" { ... }
