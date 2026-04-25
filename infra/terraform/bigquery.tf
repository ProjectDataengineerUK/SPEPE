resource "google_bigquery_dataset" "spepe_silver" {
  dataset_id                  = "spepe_silver"
  location                    = var.region
  description                 = "SPEPE Silver layer — clean, joined, schema-enforced data"
  labels                      = local.labels
  default_table_expiration_ms = 7776000000 # 90 days — Silver is rebuilt from Bronze on demand

  access {
    role          = "OWNER"
    user_by_email = var.admin_email
  }
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
    role          = "OWNER"
    user_by_email = var.admin_email
  }
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
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_municipio_eleicao"
  description              = "5570 municípios × 3 eleições × ~200 features"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = true

  range_partitioning {
    field = "ano_eleicao"
    range {
      start    = 2010
      end      = 2034
      interval = 4
    }
  }

  clustering = ["sg_uf", "cd_municipio", "ano_eleicao"]

  schema = jsonencode([
    { name = "cd_municipio", type = "INT64", mode = "REQUIRED" },
    { name = "sg_uf", type = "STRING", mode = "REQUIRED" },
    { name = "sg_regiao", type = "STRING", mode = "NULLABLE" },
    { name = "nm_municipio", type = "STRING", mode = "NULLABLE" },
    { name = "ano_eleicao", type = "INT64", mode = "REQUIRED" },
    { name = "cd_cargo", type = "INT64", mode = "NULLABLE" },
    { name = "qt_votos_total", type = "INT64", mode = "NULLABLE" },
    { name = "idhm_2010", type = "FLOAT64", mode = "NULLABLE" },
    { name = "renda_media_domiciliar", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_analfabetos", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_zona_rural", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_evangelicos", type = "FLOAT64", mode = "NULLABLE" },
    { name = "taxa_desemprego", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pib_per_capita", type = "FLOAT64", mode = "NULLABLE" },
    { name = "populacao_total", type = "INT64", mode = "NULLABLE" },
    { name = "features_json", type = "JSON", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "fact_candidato_dia" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_candidato_dia"
  description              = "Candidatos × dia × features digitais (~40 features)"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = true

  time_partitioning {
    type  = "DAY"
    field = "data"
  }

  clustering = ["sg_uf", "ano_eleicao", "nm_candidato"]

  schema = jsonencode([
    { name = "data", type = "DATE", mode = "REQUIRED" },
    { name = "nm_candidato", type = "STRING", mode = "REQUIRED" },
    { name = "sg_uf", type = "STRING", mode = "NULLABLE" },
    { name = "ano_eleicao", type = "INT64", mode = "REQUIRED" },
    { name = "qt_votos_total", type = "INT64", mode = "NULLABLE" },
    { name = "google_trends_score", type = "FLOAT64", mode = "NULLABLE" },
    { name = "youtube_views", type = "INT64", mode = "NULLABLE" },
    { name = "meta_ad_spend_brl", type = "FLOAT64", mode = "NULLABLE" },
    { name = "meta_ad_impressions", type = "INT64", mode = "NULLABLE" },
    { name = "sentiment_score", type = "FLOAT64", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "fact_pesquisa" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_pesquisa"
  description              = "Pesquisas eleitorais × house effect ajustado"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = false # volume < 10GB — full scan custa < $0.075

  time_partitioning {
    type  = "DAY"
    field = "data_pesquisa"
  }

  clustering = ["candidato", "sg_uf", "instituto"]

  schema = jsonencode([
    { name = "data_pesquisa", type = "DATE", mode = "REQUIRED" },
    { name = "instituto", type = "STRING", mode = "REQUIRED" },
    { name = "candidato", type = "STRING", mode = "REQUIRED" },
    { name = "sg_uf", type = "STRING", mode = "NULLABLE" },
    { name = "cargo", type = "STRING", mode = "NULLABLE" },
    { name = "intencao_pct", type = "FLOAT64", mode = "REQUIRED" },
    { name = "house_effect", type = "FLOAT64", mode = "NULLABLE" },
    { name = "intencao_ajustada", type = "FLOAT64", mode = "NULLABLE" },
    { name = "margem_erro", type = "FLOAT64", mode = "NULLABLE" },
    { name = "tamanho_amostra", type = "INT64", mode = "NULLABLE" },
    { name = "registro_tse", type = "STRING", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "fact_secao_eleicao" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_secao_eleicao"
  description              = "Granular: seção × candidato × cargo — base para todas as visões geográficas"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = true

  range_partitioning {
    field = "ano_eleicao"
    range {
      start    = 2010
      end      = 2034
      interval = 4
    }
  }

  clustering = ["sg_uf", "cd_municipio", "nr_zona", "nr_secao"]

  schema = jsonencode([
    { name = "sg_uf", type = "STRING", mode = "REQUIRED" },
    { name = "sg_regiao", type = "STRING", mode = "NULLABLE" },
    { name = "cd_municipio", type = "INT64", mode = "NULLABLE" },
    { name = "nm_municipio", type = "STRING", mode = "NULLABLE" },
    { name = "nr_zona", type = "INT64", mode = "NULLABLE" },
    { name = "nr_secao", type = "INT64", mode = "NULLABLE" },
    { name = "nm_candidato", type = "STRING", mode = "REQUIRED" },
    { name = "sg_partido", type = "STRING", mode = "NULLABLE" },
    { name = "cd_cargo", type = "INT64", mode = "NULLABLE" },
    { name = "nr_turno", type = "INT64", mode = "NULLABLE" },
    { name = "qt_votos", type = "INT64", mode = "NULLABLE" },
    { name = "ano_eleicao", type = "INT64", mode = "REQUIRED" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}

# Hierarquia geográfica: município → estado → região — chave de join para drill-down
resource "google_bigquery_table" "dim_territorio" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "dim_territorio"
  description         = "Dimensão geográfica: município × estado × região — 5570 linhas"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  clustering = ["sg_uf", "cd_municipio"]

  schema = jsonencode([
    { name = "cd_municipio", type = "INT64", mode = "REQUIRED" },
    { name = "nm_municipio", type = "STRING", mode = "REQUIRED" },
    { name = "cd_ibge", type = "INT64", mode = "NULLABLE" },
    { name = "sg_uf", type = "STRING", mode = "REQUIRED" },
    { name = "nm_uf", type = "STRING", mode = "NULLABLE" },
    { name = "sg_regiao", type = "STRING", mode = "REQUIRED" },
    { name = "nm_regiao", type = "STRING", mode = "REQUIRED" },
    { name = "latitude", type = "FLOAT64", mode = "NULLABLE" },
    { name = "longitude", type = "FLOAT64", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

# Visão pré-agregada por zona — evita scan de fact_secao_eleicao (~100-500M linhas) para o dashboard
resource "google_bigquery_table" "mv_zona_eleicao" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "mv_zona_eleicao"
  description         = "Materialized View: votos por zona × cargo × candidato × eleição"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  range_partitioning {
    field = "ano_eleicao"
    range {
      start    = 2010
      end      = 2034
      interval = 4
    }
  }

  clustering = ["sg_uf", "cd_municipio", "nr_zona", "cd_cargo"]

  materialized_view {
    query               = <<-EOT
      SELECT
        sg_uf,
        sg_regiao,
        cd_municipio,
        nm_municipio,
        nr_zona,
        cd_cargo,
        nm_candidato,
        sg_partido,
        nr_turno,
        ano_eleicao,
        SUM(qt_votos) AS qt_votos_total,
        COUNT(*)      AS qt_secoes
      FROM `${var.project_id}.spepe_gold.fact_secao_eleicao`
      GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
    EOT
    enable_refresh      = true
    refresh_interval_ms = 3600000
  }
}

# Segurança Pública — IVS (IPEA) + Atlas da Violência (IPEA/FBSP) + SINESP
resource "google_bigquery_table" "fact_seguranca_municipio" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_seguranca_municipio"
  description              = "Indicadores de segurança pública por município × ano — IVS + Atlas da Violência + SINESP"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = true

  range_partitioning {
    field = "ano"
    range {
      start    = 2010
      end      = 2034
      interval = 1
    }
  }

  clustering = ["sg_uf", "cd_municipio_ibge"]

  schema = jsonencode([
    { name = "cd_municipio_ibge", type = "INT64", mode = "REQUIRED" },
    { name = "sg_uf", type = "STRING", mode = "REQUIRED" },
    { name = "sg_regiao", type = "STRING", mode = "NULLABLE" },
    { name = "nm_regiao", type = "STRING", mode = "NULLABLE" },
    { name = "ano", type = "INT64", mode = "REQUIRED" },
    # Atlas da Violência — IPEA/FBSP
    { name = "taxa_homicidio_100k", type = "FLOAT64", mode = "NULLABLE" },
    { name = "qt_homicidios", type = "INT64", mode = "NULLABLE" },
    # IVS — Índice de Vulnerabilidade Social (IPEA)
    { name = "ivs_total", type = "FLOAT64", mode = "NULLABLE" },
    { name = "ivs_infraestrutura", type = "FLOAT64", mode = "NULLABLE" },
    { name = "ivs_capital_humano", type = "FLOAT64", mode = "NULLABLE" },
    { name = "ivs_renda_trabalho", type = "FLOAT64", mode = "NULLABLE" },
    # SINESP — ocorrências criminais (quando disponível)
    { name = "taxa_roubo_100k", type = "FLOAT64", mode = "NULLABLE" },
    { name = "taxa_furto_100k", type = "FLOAT64", mode = "NULLABLE" },
    { name = "qt_feminicidio", type = "INT64", mode = "NULLABLE" },
    { name = "taxa_mortalidade_transito_100k", type = "FLOAT64", mode = "NULLABLE" },
    # Metadados
    { name = "fontes", type = "STRING", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}
