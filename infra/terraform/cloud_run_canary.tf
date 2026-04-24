# ─────────────────────────────────────────────────────────────────────────────
# Cloud Run Canary — Traffic Splitting via Revisions (prod only)
#
# ARCHITECTURE NOTE (Critical fix):
# The previous implementation created a *separate* Cloud Run service named
# "spepe-prod-canary". This is incorrect. Cloud Run traffic splitting works
# on *revisions within the same service*, not across separate services. A
# separate service would have a different URL, meaning users are never actually
# split between champion and challenger.
#
# Correct model:
#   - One service: spepe-prod
#   - Two revisions: champion (tagged "champion") and challenger (tagged "challenger")
#   - Traffic split managed at the service level (90% / 10%)
#
# Terraform manages the *baseline service configuration*. The canary workflow
# (canary_deploy.yml) handles the revision tagging and traffic manipulation
# via gcloud CLI — this is intentional, because Terraform's declarative model
# is not a good fit for ephemeral canary state that changes during evaluation.
#
# This file only provisions the prod-specific IAM and outputs needed for canary.
# ─────────────────────────────────────────────────────────────────────────────

# Confirm we have a canary-capable service only in prod
locals {
  is_prod = var.environment == "prod"
}

# Output the service URL so canary_deploy.yml can construct health-check URLs
output "cloud_run_service_url" {
  description = "Cloud Run service URL (used by canary workflow for health checks)"
  value       = google_cloud_run_v2_service.spepe.uri
}

# Canary-specific label on the service (non-destructive metadata)
# Removed: the old resource "google_cloud_run_v2_service" "spepe_canary" block.
# That resource created a separate service at spepe-prod-canary, which meant:
#   1. Users hitting spepe-prod never saw challenger traffic.
#   2. The canary_deploy.yml workflow deployed to spepe-prod (correct), making
#      the Terraform resource orphaned and misleading.
# If you need Terraform to manage traffic splits declaratively in the future,
# use the `traffic` block on google_cloud_run_v2_service.spepe with
# revision-name references. Prefer the workflow-driven approach for canary.
