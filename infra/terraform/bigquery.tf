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

  lifecycle { ignore_changes = [schema] }

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
    # Faixas etárias (Censo 2022)
    { name = "pct_0_14", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_15_29", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_30_59", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_60_mais", type = "FLOAT64", mode = "NULLABLE" },
    # Gênero
    { name = "pct_mulheres", type = "FLOAT64", mode = "NULLABLE" },
    # Escolaridade
    { name = "pct_ensino_medio", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_superior_completo", type = "FLOAT64", mode = "NULLABLE" },
    # Religião (Censo 2010)
    { name = "pct_catolico", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_sem_religiao", type = "FLOAT64", mode = "NULLABLE" },
    # Urbanização
    { name = "pct_urbano", type = "FLOAT64", mode = "NULLABLE" },
    { name = "densidade_demografica", type = "FLOAT64", mode = "NULLABLE" },
    { name = "in_regiao_metropolitana", type = "BOOL", mode = "NULLABLE" },
    # Desigualdade / Pobreza
    { name = "gini_renda", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_extrema_pobreza", type = "FLOAT64", mode = "NULLABLE" },
    # Digital (CETIC TIC Domicílios)
    { name = "pct_internet_domiciliar", type = "FLOAT64", mode = "NULLABLE" },
    # Econômico (DIEESE)
    { name = "cesta_basica_uf_brl", type = "FLOAT64", mode = "NULLABLE" },
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

  lifecycle { ignore_changes = [schema] }

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

  lifecycle { ignore_changes = [schema] }

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

  lifecycle { ignore_changes = [schema] }

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

  lifecycle { ignore_changes = [schema] }

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
        cd_municipio,
        nm_municipio,
        nr_zona,
        cd_cargo,
        nm_candidato,
        sg_partido,
        nr_turno,
        ano_eleicao,
        SUM(total_votos) AS qt_votos_total,
        COUNT(*)         AS qt_secoes
      FROM `${var.project_id}.spepe_gold.fact_secao_eleicao`
      GROUP BY sg_uf, cd_municipio, nm_municipio, nr_zona,
               cd_cargo, nm_candidato, sg_partido, nr_turno, ano_eleicao
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

  lifecycle { ignore_changes = [schema] }

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

# Saúde pública — DataSUS SIM (mortalidade) + SINASC (nascimentos) + ANS (cobertura)
resource "google_bigquery_table" "fact_saude_municipio" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_saude_municipio"
  description              = "Indicadores de saúde pública por município × ano — DataSUS SIM + SINASC + ANS"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = true

  lifecycle { ignore_changes = [schema] }

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
    { name = "ano", type = "INT64", mode = "REQUIRED" },
    # SIM — Sistema de Informação sobre Mortalidade
    { name = "taxa_mortalidade_geral_100k", type = "FLOAT64", mode = "NULLABLE" },
    { name = "taxa_mortalidade_infantil_1000", type = "FLOAT64", mode = "NULLABLE" },
    { name = "taxa_mortalidade_materna_100k", type = "FLOAT64", mode = "NULLABLE" },
    { name = "qt_obitos_total", type = "INT64", mode = "NULLABLE" },
    { name = "qt_obitos_evitaveis", type = "INT64", mode = "NULLABLE" },
    # SINASC — Nascidos Vivos
    { name = "taxa_natalidade_1000", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_nascimentos_low_weight", type = "FLOAT64", mode = "NULLABLE" },
    # ANS — Cobertura planos de saúde
    { name = "pct_cobertura_plano_saude", type = "FLOAT64", mode = "NULLABLE" },
    # IDSUS / Índice de Desempenho do SUS
    { name = "idsus_score", type = "FLOAT64", mode = "NULLABLE" },
    # Metadados
    { name = "fontes", type = "STRING", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}


# ─── Silver: Dimensão páginas sociais dos candidatos ─────────────────────────
resource "google_bigquery_table" "dim_candidato_social_pages" {
  dataset_id = google_bigquery_dataset.spepe_silver.dataset_id
  table_id   = "dim_candidato_social_pages"
  project    = var.project_id

  time_partitioning {
    type  = "DAY"
    field = "dt_atualizacao"
  }

  schema = jsonencode([
    { name = "candidato_id",       type = "STRING", mode = "REQUIRED" },
    { name = "nome_candidato",     type = "STRING", mode = "REQUIRED" },
    { name = "facebook_page_id",   type = "STRING", mode = "NULLABLE" },
    { name = "instagram_handle",   type = "STRING", mode = "NULLABLE" },
    { name = "youtube_channel_id", type = "STRING", mode = "NULLABLE" },
    { name = "twitter_handle",     type = "STRING", mode = "NULLABLE" },
    { name = "tiktok_handle",      type = "STRING", mode = "NULLABLE" },
    { name = "followers_fb",       type = "INT64",  mode = "NULLABLE" },
    { name = "is_verified",        type = "BOOL",   mode = "NULLABLE" },
    { name = "dt_atualizacao",     type = "DATE",   mode = "REQUIRED" }
  ])

  deletion_protection = false
}

# ─── Silver: Social ───────────────────────────────────────────────────────────
resource "google_bigquery_table" "silver_social_mencoes_br" {
  dataset_id          = google_bigquery_dataset.spepe_silver.dataset_id
  table_id            = "social_mencoes_br"
  description         = "Menções sociais brutas por post/tweet — Silver layer (Twitter/X + Facebook)"
  labels              = local.labels
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }

  clustering = ["fonte", "sg_uf"]

  schema = jsonencode([
    { name = "id_post", type = "STRING", mode = "NULLABLE" },
    { name = "fonte", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "text", type = "STRING", mode = "NULLABLE" },
    { name = "sg_uf", type = "STRING", mode = "NULLABLE" },
    { name = "like_count", type = "INT64", mode = "NULLABLE" },
    { name = "retweet_count", type = "INT64", mode = "NULLABLE" },
    { name = "reply_count", type = "INT64", mode = "NULLABLE" },
    { name = "likes", type = "INT64", mode = "NULLABLE" },
    { name = "comments", type = "INT64", mode = "NULLABLE" },
    { name = "shares", type = "INT64", mode = "NULLABLE" },
    { name = "ano", type = "INT64", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

# ─── Gold: Social agregado ────────────────────────────────────────────────────
resource "google_bigquery_table" "fact_social_municipio" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_social_municipio"
  description              = "Engajamento social agregado por UF × dia × fonte — Gold layer"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = false

  lifecycle { ignore_changes = [schema] }

  time_partitioning {
    type  = "DAY"
    field = "data_referencia"
  }

  clustering = ["sg_uf", "fonte"]

  schema = jsonencode([
    { name = "sg_uf", type = "STRING", mode = "REQUIRED" },
    { name = "fonte", type = "STRING", mode = "REQUIRED" },
    { name = "data_referencia", type = "DATE", mode = "REQUIRED" },
    { name = "ano", type = "INT64", mode = "NULLABLE" },
    { name = "qt_posts", type = "INT64", mode = "NULLABLE" },
    { name = "total_likes", type = "INT64", mode = "NULLABLE" },
    { name = "total_shares", type = "INT64", mode = "NULLABLE" },
    { name = "total_comments", type = "INT64", mode = "NULLABLE" },
    { name = "total_retweets", type = "INT64", mode = "NULLABLE" },
    { name = "total_engajamento", type = "INT64", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

# ─── Gold: IBGE indicadores municipais ───────────────────────────────────────
resource "google_bigquery_table" "fact_ibge_municipio" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_ibge_municipio"
  description              = "Indicadores socioeconômicos IBGE por município — Gold layer"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = false

  lifecycle { ignore_changes = [schema] }

  range_partitioning {
    field = "ano"
    range {
      start    = 2000
      end      = 2031
      interval = 1
    }
  }

  clustering = ["sg_uf", "cd_municipio_ibge"]

  schema = jsonencode([
    { name = "cd_municipio_ibge", type = "INT64", mode = "REQUIRED" },
    { name = "nm_municipio", type = "STRING", mode = "NULLABLE" },
    { name = "sg_uf", type = "STRING", mode = "REQUIRED" },
    { name = "sg_regiao", type = "STRING", mode = "NULLABLE" },
    { name = "ano", type = "INT64", mode = "REQUIRED" },
    { name = "idhm", type = "FLOAT64", mode = "NULLABLE" },
    { name = "idhm_educacao", type = "FLOAT64", mode = "NULLABLE" },
    { name = "idhm_longevidade", type = "FLOAT64", mode = "NULLABLE" },
    { name = "idhm_renda", type = "FLOAT64", mode = "NULLABLE" },
    { name = "renda_per_capita", type = "FLOAT64", mode = "NULLABLE" },
    { name = "gini", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_extrema_pobreza", type = "FLOAT64", mode = "NULLABLE" },
    { name = "taxa_analfabetismo", type = "FLOAT64", mode = "NULLABLE" },
    { name = "anos_estudo_medio", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_domicilios_agua", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_domicilios_esgoto", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_domicilios_energia", type = "FLOAT64", mode = "NULLABLE" },
    { name = "populacao_total", type = "INT64", mode = "NULLABLE" },
    { name = "densidade_demografica", type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_urbano", type = "FLOAT64", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

# ─── Gold: Sentimento geolocalizado ─────────────────────────────────────────
resource "google_bigquery_table" "fact_sentiment_municipio" {
  dataset_id               = google_bigquery_dataset.spepe_gold.dataset_id
  table_id                 = "fact_sentiment_municipio"
  description              = "Sentimento político por município × dia × candidato — NER via Gemini 2.0 Flash"
  labels                   = local.labels
  deletion_protection      = var.environment == "prod"
  require_partition_filter = false

  lifecycle { ignore_changes = [schema] }

  time_partitioning {
    type  = "DAY"
    field = "data_referencia"
  }

  clustering = ["sg_uf", "cd_municipio_ibge"]

  schema = jsonencode([
    { name = "data_referencia",  type = "DATE",      mode = "REQUIRED" },
    { name = "cd_municipio_ibge", type = "INT64",    mode = "REQUIRED" },
    { name = "sg_uf",            type = "STRING",    mode = "REQUIRED" },
    { name = "candidato",        type = "STRING",    mode = "NULLABLE" },
    { name = "sentimento_score", type = "FLOAT64",   mode = "NULLABLE" },
    { name = "volume_mencoes",   type = "INT64",     mode = "NULLABLE" },
    { name = "polaridade_neg",   type = "INT64",     mode = "NULLABLE" },
    { name = "polaridade_pos",   type = "INT64",     mode = "NULLABLE" },
    { name = "polaridade_neu",   type = "INT64",     mode = "NULLABLE" },
    { name = "fontes",           type = "STRING",    mode = "REPEATED" },
    { name = "ingested_at",      type = "TIMESTAMP", mode = "NULLABLE" }
  ])
}

# Gold: indicadores econômicos municipais — DIEESE + CETIC + PIB IBGE + desigualdade
resource "google_bigquery_table" "fact_economico_municipio" {
  dataset_id          = google_bigquery_dataset.spepe_gold.dataset_id
  table_id            = "fact_economico_municipio"
  description         = "Indicadores econômicos municipais — DIEESE cesta básica + CETIC + PIB IBGE"
  labels              = local.labels
  deletion_protection = var.environment == "prod"

  lifecycle { ignore_changes = [schema] }

  range_partitioning {
    field = "ano"
    range {
      start    = 2000
      end      = 2031
      interval = 1
    }
  }

  clustering = ["sg_uf", "cd_municipio_ibge"]

  schema = jsonencode([
    { name = "cd_municipio_ibge",          type = "INT64",   mode = "REQUIRED" },
    { name = "sg_uf",                      type = "STRING",  mode = "REQUIRED" },
    { name = "ano",                        type = "INT64",   mode = "REQUIRED" },
    { name = "data_referencia",            type = "DATE",    mode = "NULLABLE" },
    { name = "cesta_basica_capital_brl",   type = "FLOAT64", mode = "NULLABLE" },
    { name = "variacao_cesta_mensal_pct",  type = "FLOAT64", mode = "NULLABLE" },
    { name = "horas_trabalho_cesta",       type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_internet_domiciliar",    type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_computador_domiciliar",  type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_smartphone_domiciliar",  type = "FLOAT64", mode = "NULLABLE" },
    { name = "pib_per_capita_brl",         type = "FLOAT64", mode = "NULLABLE" },
    { name = "gini_renda",                 type = "FLOAT64", mode = "NULLABLE" },
    { name = "pct_extrema_pobreza",        type = "FLOAT64", mode = "NULLABLE" },
    { name = "granularidade",              type = "STRING",  mode = "NULLABLE" },
    { name = "fontes",                     type = "STRING",  mode = "NULLABLE" },
    { name = "ingested_at",                type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}
