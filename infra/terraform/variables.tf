variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for data services (LGPD: southamerica-east1)"
  type        = string
  default     = "southamerica-east1"
}

variable "vertex_region" {
  description = "Vertex AI region (us-central1 — Vertex not in southamerica-east1)"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment: dev, staging, prod"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "admin_email" {
  description = "Admin email for IAP and alerts"
  type        = string
}

variable "budget_alert_usd" {
  description = "Monthly budget alert threshold in USD"
  type        = number
  default     = 50
}

variable "app_image" {
  description = "Full Artifact Registry image URI including tag (e.g. southamerica-east1-docker.pkg.dev/PROJECT/spepe/app:SHA)"
  type        = string
  # No default — must be supplied explicitly to prevent accidental :latest deploys.
  # Pass via: -var="app_image=..."  or  TF_VAR_app_image env var in CI.
}

variable "github_repo" {
  description = "GitHub repository in 'owner/repo' format for WIF subject binding"
  type        = string
}

variable "billing_account_id" {
  description = "GCP Billing Account ID (format: XXXXXX-XXXXXX-XXXXXX) for budget alerts"
  type        = string
}

variable "domain" {
  description = "Public domain for the HTTPS Load Balancer (e.g. spepe.example.com). Required for staging/prod IAP."
  type        = string
  default     = ""
}

variable "research_group_email" {
  description = "Google Group email for IAP access (optional, staging/prod only)"
  type        = string
  default     = ""
}

variable "use_iap" {
  description = "Enable IAP + HTTPS LB. Requires project under Google Workspace org. Set false for personal GCP projects."
  type        = bool
  default     = false
}
