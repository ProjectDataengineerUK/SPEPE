resource "google_bigquery_dataset" "spepe_silver" {
  dataset_id                  = "spepe_silver"
  location                    = var.region
  description                 = "SPEPE Silver layer — clean, joined, schema-enforced data"
  labels                      = local.labels
  default_table_expiration_ms = 7776000000 # 90 days — Silver is rebuilt from Bronze on demand

  access {
    role          = "WRITER"
    user_by_email = google_service_account.dataops_jobs.email
  }
  access {
    role          = "READER"
    user_by_email = google_service_account.cloud_run.email
  }
}

resource "google_bigquery_dataset" "spepe_gold" {
  dataset_id  = "spepe_gold"
  location    = var.region
  description = "SPEPE Gold layer — 3 fact tables for ML and analytics"
  labels      = local.labels

  access {
    role          = "WRITER"
    user_by_email = google_service_account.dataops_jobs.email
  }
  access {
    role          = "READER"
    user_by_email = google_service_account.cloud_run.email
  }
}

resource "google_bigquery_table" "fact_municipio_eleicao" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "fact_municipio_eleicao"
  description         = "5570 municípios × 3 eleições × ~200 features"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type                     = "YEAR"
    field                    = "ano_eleicao"
    require_partition_filter = true
  }

  clustering = ["sg_uf", "cd_municipio", "ano_eleicao"]

  schema = jsonencode([
    { name = "cd_municipio",             type = "INT64",   mode = "REQUIRED" },
    { name = "sg_uf",                    type = "STRING",  mode = "REQUIRED" },
    { name = "nm_municipio",             type = "STRING",  mode = "NULLABLE" },
    { name = "ano_eleicao",              type = "INT64",   mode = "REQUIRED" },
    { name = "qt_votos_total",           type = "INT64",   mode = "NULLABLE" },
    { name = "idhm_2010",                type = "FLOAT64", mode = "NULLABLE" },
    { name = "renda_media_domiciliar",   type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_analfabetos",          type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_zona_rural",           type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_evangelicos",          type = "FLOAT64", mode = "NULLABLE" },
    { name = "taxa_desemprego",          type = "FLOAT64", mode = "NULLABLE" },
    { name = "pib_per_capita",           type = "FLOAT64", mode = "NULLABLE" },
    { name = "populacao_total",          type = "INT64",   mode = "NULLABLE" },
    { name = "features_json",            type = "JSON",    mode = "NULLABLE" },
    { name = "ingested_at",              type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "fact_candidato_dia" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "fact_candidato_dia"
  description         = "Candidatos × dia × features digitais (~40 features)"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type                     = "DAY"
    field                    = "data"
    require_partition_filter = true
  }

  clustering = ["nm_candidato", "sg_uf", "ano_eleicao"]

  schema = jsonencode([
    { name = "data",                     type = "DATE",    mode = "REQUIRED" },
    { name = "nm_candidato",             type = "STRING",  mode = "REQUIRED" },
    { name = "sg_uf",                    type = "STRING",  mode = "NULLABLE" },
    { name = "ano_eleicao",              type = "INT64",   mode = "REQUIRED" },
    { name = "qt_votos_total",           type = "INT64",   mode = "NULLABLE" },
    { name = "google_trends_score",      type = "FLOAT64", mode = "NULLABLE" },
    { name = "youtube_views",            type = "INT64",   mode = "NULLABLE" },
    { name = "meta_ad_spend_brl",        type = "FLOAT64", mode = "NULLABLE" },
    { name = "meta_ad_impressions",      type = "INT64",   mode = "NULLABLE" },
    { name = "sentiment_score",          type = "FLOAT64", mode = "NULLABLE" },
    { name = "ingested_at",              type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "fact_pesquisa" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "fact_pesquisa"
  description         = "Pesquisas eleitorais × house effect ajustado"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type                     = "DAY"
    field                    = "data_pesquisa"
    require_partition_filter = true
  }

  clustering = ["instituto", "candidato", "sg_uf"]

  schema = jsonencode([
    { name = "data_pesquisa",            type = "DATE",    mode = "REQUIRED" },
    { name = "instituto",                type = "STRING",  mode = "REQUIRED" },
    { name = "candidato",                type = "STRING",  mode = "REQUIRED" },
    { name = "sg_uf",                    type = "STRING",  mode = "NULLABLE" },
    { name = "cargo",                    type = "STRING",  mode = "NULLABLE" },
    { name = "intencao_pct",             type = "FLOAT64", mode = "REQUIRED" },
    { name = "house_effect",             type = "FLOAT64", mode = "NULLABLE" },
    { name = "intencao_ajustada",        type = "FLOAT64", mode = "NULLABLE" },
    { name = "margem_erro",              type = "FLOAT64", mode = "NULLABLE" },
    { name = "tamanho_amostra",          type = "INT64",   mode = "NULLABLE" },
    { name = "registro_tse",             type = "STRING",  mode = "NULLABLE" },
    { name = "ingested_at",              type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "fact_secao_eleicao" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "fact_secao_eleicao"
  description         = "Granular: seção × candidato × cargo — base para todas as visões geográficas"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type                     = "YEAR"
    field                    = "ano_eleicao"
    require_partition_filter = true
  }

  clustering = ["sg_uf", "cd_municipio", "nr_zona"]

  schema = jsonencode([
    { name = "sg_uf",        type = "STRING",    mode = "REQUIRED" },
    { name = "sg_regiao",    type = "STRING",    mode = "NULLABLE" },
    { name = "cd_municipio", type = "INT64",     mode = "NULLABLE" },
    { name = "nm_municipio", type = "STRING",    mode = "NULLABLE" },
    { name = "nr_zona",      type = "INT64",     mode = "NULLABLE" },
    { name = "nr_secao",     type = "INT64",     mode = "NULLABLE" },
    { name = "nm_candidato", type = "STRING",    mode = "REQUIRED" },
    { name = "sg_partido",   type = "STRING",    mode = "NULLABLE" },
    { name = "cd_cargo",     type = "INT64",     mode = "NULLABLE" },
    { name = "nr_turno",     type = "INT64",     mode = "NULLABLE" },
    { name = "qt_votos",     type = "INT64",     mode = "NULLABLE" },
    { name = "ano_eleicao",  type = "INT64",     mode = "REQUIRED" },
    { name = "ingested_at",  type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}
