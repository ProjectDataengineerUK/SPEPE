resource "google_firestore_database" "spepe" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  deletion_policy = var.environment == "prod" ? "DELETE" : "ABANDON"
}

resource "google_firestore_index" "session_memory" {
  project    = var.project_id
  collection = "spepe_memory"
  database   = google_firestore_database.spepe.name

  fields {
    field_path = "session_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "timestamp"
    order      = "DESCENDING"
  }
}
