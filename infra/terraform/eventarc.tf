resource "google_eventarc_trigger" "drift_retrain" {
  name     = "spepe-drift-retrain-trigger"
  location = var.region
  labels   = local.labels

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  matching_criteria {
    attribute = "resourceName"
    value     = google_pubsub_topic.drift_detected.id
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

resource "google_eventarc_trigger" "model_canary" {
  name     = "spepe-model-canary-trigger"
  location = var.region
  labels   = local.labels

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.aiplatform.v1.ModelService.UploadModel"
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.spepe.name
      region  = var.region
      path    = "/jobs/canary-start"
    }
  }

  service_account = google_service_account.cloud_run.email

  depends_on = [google_cloud_run_v2_service.spepe]
}
