locals {
  # lb_enabled:  HTTPS LB + custom domain + Google-managed SSL cert.
  #              Requires only var.domain != "".  Works on personal GCP projects.
  lb_enabled = var.domain != "" && var.environment != "dev"

  # iap_enabled: lb_enabled + IAP auth layer.
  #              Requires var.use_iap=true AND project under Google Workspace org.
  iap_enabled = local.lb_enabled && var.use_iap
}

# ── IAP Brand + OAuth Client (Workspace org only) ────────────────────────────

resource "google_iap_brand" "spepe" {
  count             = local.iap_enabled ? 1 : 0
  support_email     = var.admin_email
  application_title = "SPEPE — Plataforma de Inteligência Eleitoral"
  project           = var.project_id
}

resource "google_iap_client" "spepe" {
  count        = local.iap_enabled ? 1 : 0
  display_name = "SPEPE IAP Client"
  brand        = google_iap_brand.spepe[0].name
}

resource "google_secret_manager_secret" "iap_client_secret" {
  count     = local.iap_enabled ? 1 : 0
  secret_id = "IAP_CLIENT_SECRET"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret_version" "iap_client_secret" {
  count       = local.iap_enabled ? 1 : 0
  secret      = google_secret_manager_secret.iap_client_secret[0].id
  secret_data = google_iap_client.spepe[0].secret
}

resource "google_secret_manager_secret_iam_member" "cloud_run_iap_secret" {
  count     = local.iap_enabled ? 1 : 0
  secret_id = google_secret_manager_secret.iap_client_secret[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ── External HTTPS Load Balancer (lb_enabled — personal GCP ok) ──────────────

resource "google_compute_global_address" "spepe" {
  count   = local.lb_enabled ? 1 : 0
  name    = "spepe-lb-ip"
  project = var.project_id
}

resource "google_compute_managed_ssl_certificate" "spepe" {
  count   = local.lb_enabled ? 1 : 0
  name    = "spepe-ssl-cert-v2"
  project = var.project_id

  managed {
    domains = [var.domain, "www.${var.domain}"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Serverless NEG connects the LB backend to the Cloud Run service
resource "google_compute_region_network_endpoint_group" "spepe" {
  count                 = local.lb_enabled ? 1 : 0
  name                  = "spepe-serverless-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  project               = var.project_id

  cloud_run {
    service = google_cloud_run_v2_service.spepe.name
  }
}

resource "google_compute_backend_service" "spepe" {
  count                 = local.lb_enabled ? 1 : 0
  name                  = "spepe-backend"
  project               = var.project_id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.spepe[0].id
  }

  # IAP block only when use_iap=true (requires Google Workspace org)
  dynamic "iap" {
    for_each = local.iap_enabled ? [1] : []
    content {
      oauth2_client_id     = google_iap_client.spepe[0].client_id
      oauth2_client_secret = google_iap_client.spepe[0].secret
    }
  }
}

resource "google_compute_url_map" "spepe" {
  count           = local.lb_enabled ? 1 : 0
  name            = "spepe-url-map"
  project         = var.project_id
  default_service = google_compute_backend_service.spepe[0].id
}

resource "google_compute_target_https_proxy" "spepe" {
  count            = local.lb_enabled ? 1 : 0
  name             = "spepe-https-proxy"
  project          = var.project_id
  url_map          = google_compute_url_map.spepe[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.spepe[0].id]
}

resource "google_compute_global_forwarding_rule" "spepe" {
  count                 = local.lb_enabled ? 1 : 0
  name                  = "spepe-forwarding-rule"
  project               = var.project_id
  target                = google_compute_target_https_proxy.spepe[0].id
  ip_address            = google_compute_global_address.spepe[0].id
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# ── IAP Access Policy (Workspace org only) ────────────────────────────────────

resource "google_iap_web_backend_service_iam_member" "admin" {
  count               = local.iap_enabled ? 1 : 0
  project             = var.project_id
  web_backend_service = google_compute_backend_service.spepe[0].name
  role                = "roles/iap.httpsResourceAccessor"
  member              = "user:${var.admin_email}"
}

resource "google_iap_web_backend_service_iam_member" "research_group" {
  count               = local.iap_enabled && var.research_group_email != "" ? 1 : 0
  project             = var.project_id
  web_backend_service = google_compute_backend_service.spepe[0].name
  role                = "roles/iap.httpsResourceAccessor"
  member              = "group:${var.research_group_email}"
}

# IAP service agent must be able to invoke Cloud Run
resource "google_project_iam_member" "iap_run_invoker" {
  count   = local.iap_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:service-${data.google_project.spepe.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "load_balancer_ip" {
  value       = local.lb_enabled ? google_compute_global_address.spepe[0].address : null
  description = "External LB IP — point your domain A record here before SSL cert can provision"
}

output "iap_client_id" {
  value       = local.iap_enabled ? google_iap_client.spepe[0].client_id : null
  description = "IAP OAuth2 client ID — set as IAP_CLIENT_ID env var in the app"
  sensitive   = false
}
