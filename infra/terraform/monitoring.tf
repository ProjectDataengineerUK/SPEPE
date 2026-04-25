resource "google_monitoring_notification_channel" "email" {
  display_name = "SPEPE Admin Email"
  type         = "email"
  labels = {
    email_address = var.admin_email
  }
}

resource "google_billing_budget" "spepe_monthly" {
  billing_account = data.google_billing_account.acct.id
  display_name    = "SPEPE Monthly Budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_alert_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.9
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }
}

data "google_billing_account" "acct" {
  billing_account = var.billing_account_id
}

resource "google_logging_project_sink" "spepe_audit" {
  name        = "spepe-audit-sink"
  destination = "storage.googleapis.com/${google_storage_bucket.spepe_data.name}/audit-logs"
  filter      = "logName:(spepe-audit OR spepe-dataops OR spepe-llmops)"

  unique_writer_identity = true
}
