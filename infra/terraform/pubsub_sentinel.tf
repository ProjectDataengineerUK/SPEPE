# Pub/Sub topics and subscriptions for Sentinel event routing

# --- Input topic: all domain events fan-in here ---
resource "google_pubsub_topic" "sentinel_events" {
  name   = "sentinel-events-${var.environment}"
  labels = local.labels

  message_retention_duration = "86400s"
}

# --- Output topic: sentinel dispatches alerts here ---
resource "google_pubsub_topic" "sentinel_alerts" {
  name   = "sentinel-alerts-${var.environment}"
  labels = local.labels

  message_retention_duration = "86400s"
}

# --- Dead-letter topic for unprocessable events ---
resource "google_pubsub_topic" "sentinel_dlq" {
  name   = "sentinel-dlq-${var.environment}"
  labels = local.labels
}

# --- Push subscription: sentinel_events → Cloud Run Sentinel ---
resource "google_pubsub_subscription" "sentinel_events_sub" {
  name   = "sentinel-events-sub-${var.environment}"
  topic  = google_pubsub_topic.sentinel_events.name
  labels = local.labels

  ack_deadline_seconds       = 60
  message_retention_duration = "3600s"

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.sentinel_dlq.id
    max_delivery_attempts = 5
  }

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.sentinel.uri}/sentinel/event"

    oidc_token {
      service_account_email = google_service_account.cloud_run.email
    }
  }

  depends_on = [google_cloud_run_v2_service.sentinel]
}

# --- Pull subscription for alerts (Looker Studio / dashboards) ---
resource "google_pubsub_subscription" "sentinel_alerts_pull" {
  name   = "sentinel-alerts-pull-${var.environment}"
  topic  = google_pubsub_topic.sentinel_alerts.name
  labels = local.labels

  ack_deadline_seconds       = 30
  message_retention_duration = "604800s"  # 7 days
}

# --- Forward domain topics to sentinel_events via subscriptions ---
# DQ violations
resource "google_pubsub_subscription" "dq_to_sentinel" {
  name  = "spepe-dq-sentinel-fwd-${var.environment}"
  topic = google_pubsub_topic.drift_detected.name

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.sentinel.uri}/sentinel/event"

    oidc_token {
      service_account_email = google_service_account.cloud_run.email
    }
  }

  ack_deadline_seconds = 30

  retry_policy {
    minimum_backoff = "5s"
    maximum_backoff = "60s"
  }
}

# --- IAM: Cloud Run SA can publish to sentinel_events ---
resource "google_pubsub_topic_iam_member" "sentinel_events_publisher" {
  topic  = google_pubsub_topic.sentinel_events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_pubsub_topic_iam_member" "sentinel_alerts_publisher" {
  topic  = google_pubsub_topic.sentinel_alerts.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}
