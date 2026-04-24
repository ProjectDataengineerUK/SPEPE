terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  # IMPORTANT: Terraform backend blocks do NOT support variable interpolation.
  # The bucket name must be a literal string. Set it via -backend-config at init time:
  #   terraform init -backend-config="bucket=YOUR_PROJECT_ID-terraform-state"
  # Or use a backend.hcl file and pass: terraform init -backend-config=backend.hcl
  backend "gcs" {
    # bucket is intentionally left unconfigured here — pass via -backend-config
    prefix = "spepe"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  labels = {
    project     = "spepe"
    environment = var.environment
    managed_by  = "terraform"
  }
}
