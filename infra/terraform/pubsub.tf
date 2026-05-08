resource "google_pubsub_topic" "drift_detected" {
  name   = "spepe-drift-detected"
  labels = local.labels

  message_retention_duration = "86400s"  # 24h
}

resource "google_pubsub_subscription" "drift_eventarc" {
  name   = "spepe-drift-eventarc-sub"
  topic  = google_pubsub_topic.drift_detected.name
  labels = local.labels

  ack_deadline_seconds       = 60
  message_retention_duration = "86400s"

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.spepe.uri}/jobs/retrain-trigger"

    oidc_token {
      service_account_email = google_service_account.cloud_run.email
    }
  }
}

resource "google_pubsub_topic_iam_member" "drift_publisher" {
  topic  = google_pubsub_topic.drift_detected.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ──────────────────────────────────────────────────────────────────────────
# Sentinel real-time events — consumed by dashboard_api.py SSE endpoint
# ──────────────────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "sentinel_events" {
  name   = "spepe-sentinel-events"
  labels = local.labels

  message_retention_duration = "3600s" # 1h
}

resource "google_pubsub_subscription" "sentinel_events_pull" {
  name   = "spepe-sentinel-events-sub"
  topic  = google_pubsub_topic.sentinel_events.name
  labels = local.labels

  ack_deadline_seconds       = 30
  message_retention_duration = "3600s"

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic_iam_member" "sentinel_publisher" {
  topic  = google_pubsub_topic.sentinel_events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_pubsub_subscription_iam_member" "sentinel_subscriber" {
  subscription = google_pubsub_subscription.sentinel_events_pull.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.cloud_run.email}"
}
