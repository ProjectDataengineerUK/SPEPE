output "cloud_run_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.spepe.uri
}

output "gcs_bucket" {
  description = "GCS data bucket name"
  value       = google_storage_bucket.spepe_data.name
}

output "bigquery_silver_dataset" {
  description = "BigQuery Silver dataset ID"
  value       = google_bigquery_dataset.spepe_silver.dataset_id
}

output "bigquery_gold_dataset" {
  description = "BigQuery Gold dataset ID"
  value       = google_bigquery_dataset.spepe_gold.dataset_id
}

output "cloud_run_sa" {
  description = "Cloud Run service account email"
  value       = google_service_account.cloud_run.email
}

output "dataops_sa" {
  description = "DataOps jobs service account email"
  value       = google_service_account.dataops_jobs.email
}

output "domain_mapping_records" {
  description = "DNS records to add at your registrar (only when var.domain is set)"
  value = var.domain != "" ? [
    for r in google_cloud_run_domain_mapping.spepe[0].status[0].resource_records : {
      type  = r.type
      name  = r.name
      value = r.rrdata
    }
  ] : []
}
