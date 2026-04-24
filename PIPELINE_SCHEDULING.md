# Pipeline Scheduling — Configuração de Atualizações

Documentação de como estão configuradas as atualizações automáticas de dados no SPEPE v1.0.0.

---

## 📊 Arquitetura Atual

```
Cloud Scheduler (não configurado ainda)
        ↓
Pub/Sub Topic (trigger)
        ↓
Cloud Run Job (TSE/IBGE/Digital ingest)
        ↓
Bronze Layer (GCS parquet)
        ↓
Cloud Run Job (Silver transform + DQ gate)
        ↓
Silver Layer (BigQuery)
        ↓
Cloud Run Job (Gold aggregation)
        ↓
Gold Layer (BigQuery — fact tables)
        ↓
Eventarc (drift detection)
        ↓
Auto-retrain (MLOps, Fase 2)
```

---

## 🎯 Jobs Configurados

### 1. **TSE Ingest Job**

**Arquivo:** `dataops/jobs/tse_ingest_job.py`

**Função:** Baixa dados TSE de um UF × ano e escreve em Bronze

**Entrypoint:**
```bash
python -m dataops.jobs.tse_ingest_job --uf SP --year 2022
```

**Ambiente:**
```bash
DEFAULT_UF=SP
DEFAULT_ANO=2022
GCS_BUCKET=spepe_data  # Se vazio, usa local
USE_BIGQUERY=false
```

**Timeout:** 3600s (1h)  
**Memory:** 2Gi  
**CPU:** 2

**Output:**
```
gs://spepe_data/raw/tse/2022/SP/resultados_SP_2022.parquet
```

**Erro handling:**
- Falha no download → exit(1)
- DataFrame vazio → exit(1)
- Success → log path e row count

---

### 2. **IBGE Sync Job**

**Arquivo:** `dataops/jobs/ibge_sync_job.py`

**Função:** Sincroniza indicadores socioeconômicos IBGE por UF

**Entrypoint:**
```bash
python -m dataops.jobs.ibge_sync_job --uf SP
```

**APIs usadas:**
- SIDRA (Censo 2010 + indicadores)
- Localidades API (municípios)

**Timeout:** 1800s (30min)  
**Memory:** 1Gi  
**CPU:** 1

**Output:**
```
gs://spepe_data/raw/ibge/{year}/SP/indicadores_SP.parquet
```

---

### 3. **Silver Transform Job**

**Arquivo:** `dataops/jobs/silver_transform_job.py`

**Função:** Bronze → Silver (normaliza + join TSE+IBGE + DQ checks)

**Entrypoint:**
```bash
python -m dataops.jobs.silver_transform_job --uf SP --years 2014 2018 2022
```

**Data Quality Gate:**
```python
DQ_THRESHOLD = float(os.environ.get("DQ_SCORE_THRESHOLD", "95.0"))
# Se score < 95%, exit(1) (bloqueia Gold build)
```

**Timeout:** 1800s (30min)  
**Memory:** 2Gi  
**CPU:** 2

**Output:**
```
bigquery:spepe_silver.tse_sp_2014
bigquery:spepe_silver.tse_sp_2018
bigquery:spepe_silver.tse_sp_2022
```

**Validações:**
- ✅ Normaliza colunas (lowercase)
- ✅ Join IBGE via de-para municipios
- ✅ Data Quality checks (nulls, outliers, duplicates)
- ✅ Coluna `cd_cargo` preservada (multi-cargo filtering)

---

### 4. **Gold Build Job**

**Arquivo:** `dataops/jobs/gold_build_job.py`

**Função:** Silver → Gold (agregações por nível geográfico + temporal)

**Entrypoint:**
```bash
python -m dataops.jobs.gold_build_job
```

**Fact Tables criados:**
1. `fact_municipio_eleicao` — por município × ano × cargo × turno
2. `fact_secao_eleicao` — por seção × zona × ano
3. `fact_candidato_dia` — por candidato × ano (time series stub)
4. `fact_pesquisa` — pesquisas eleitorais agregadas

**Timeout:** 1800s (30min)  
**Memory:** 2Gi  
**CPU:** 2

**Output:**
```
bigquery:spepe_gold.fact_municipio_eleicao (particionado por ano_eleicao, clusterizado por sg_uf)
bigquery:spepe_gold.fact_secao_eleicao
bigquery:spepe_gold.fact_candidato_dia
bigquery:spepe_gold.fact_pesquisa
```

---

### 5. **Digital Ingest Job** (Fase 2)

**Arquivo:** `dataops/jobs/digital_ingest_job.py`

**Função:** Google Trends + Meta Ads + YouTube (não em Fase 1)

**Status:** Implementação pendente, schema pronto

---

## ⏰ Agendamento — ATUAL vs FUTURO

### Atual (v1.0.0)

**Tudo manual:**
```bash
# Executar job uma vez (dev)
gcloud run jobs execute spepe-tse-ingest \
  --project=$PROJECT_ID \
  --region=$REGION

# Ver logs
gcloud run jobs log spepe-tse-ingest \
  --project=$PROJECT_ID \
  --region=$REGION
```

### Futuro (após v1.0.0)

Implementar agendamento via **Cloud Scheduler + Pub/Sub**:

```hcl
# Exemplo Terraform — NÃO está implementado ainda
resource "google_cloud_scheduler_job" "daily_tse_ingest" {
  name        = "spepe-daily-tse-ingest"
  schedule    = "0 2 * * *"  # 2am diariamente
  time_zone   = "America/Sao_Paulo"
  region      = var.region
  
  pubsub_target {
    topic_name = google_pubsub_topic.tse_ingest_trigger.id
    data       = base64encode(jsonencode({
      uf   = "SP"
      year = 2022
    }))
  }
}

# Trigger: Pub/Sub → Job
resource "google_eventarc_trigger" "tse_ingest" {
  name     = "spepe-tse-ingest-trigger"
  location = var.region
  
  matching_criteria {
    attribute = "resourceName"
    value     = google_pubsub_topic.tse_ingest_trigger.id
  }
  
  destination {
    cloud_run_job {
      job    = google_cloud_run_v2_job.spepe_jobs["tse_ingest"].name
      region = var.region
    }
  }
}
```

---

## 🔄 Pipeline de Dados — Fluxo Completo

### Cenário: Ingerir SP 2022 (início ao fim)

```bash
# 1. Download TSE
gcloud run jobs execute spepe-tse-ingest \
  --args="--uf,SP,--year,2022"

# → Bronze: gs://spepe_data/raw/tse/2022/SP/resultados_SP_2022.parquet

# 2. Sincronizar IBGE
gcloud run jobs execute spepe-ibge-sync \
  --args="--uf,SP"

# → Bronze: gs://spepe_data/raw/ibge/2022/SP/indicadores_SP.parquet

# 3. Transformar para Silver (normalizar + join)
gcloud run jobs execute spepe-silver-transform \
  --args="--uf,SP,--years,2022"

# → Silver (BigQuery): tse_sp_2022 (normalized + IBGE joined)
# → DQ score: 95.3% ✅

# 4. Build Gold (agregações)
gcloud run jobs execute spepe-gold-build

# → Gold (BigQuery): 
#   - fact_municipio_eleicao (35001, SP, 2022, Presidente) = 100k votos
#   - fact_secao_eleicao (35001, zona 1, secao 1) = 1.2k votos
#   - fact_candidato_dia (Candidato X, 2022) = 5.2M votos total
```

---

## 🔐 Data Quality Gates

### Silver Transform — DQ Validation

```python
# checks feitos:
# 1. Row count >= min threshold
if len(df) < MIN_ROWS:
    score -= 20

# 2. No critical nulls
critical_cols = ["cd_municipio", "sg_uf", "qt_votos", "nm_candidato"]
if df[critical_cols].isna().any():
    score -= 30

# 3. No duplicates
if df.duplicated().any():
    score -= 15

# 4. Value range checks
if (df["qt_votos"] < 0).any() or (df["qt_votos"] > 1e9).any():
    score -= 25

# Score final: 0-100
# Bloqueio Gold se < 95%
```

### Gold Build — Validation

```python
# Verificar que prefixos_keys existem
if municipality_key not in df.columns:
    log.warning("No municipality key found")
    return pd.DataFrame()

# Verificar groupby columns estão disponíveis
avail_group = [c for c in group_cols if c in df.columns]
if not avail_group:
    return df  # Fallback: retorna ungrouped
```

---

## 📈 Monitoring & Alerting

### Logs

```bash
# Ver logs de um job
gcloud run jobs log spepe-tse-ingest \
  --project=$PROJECT_ID \
  --region=$REGION \
  --tail=100  # últimas 100 linhas

# Logs estruturados (Cloud Logging)
gcloud logging read "resource.type=cloud_run_job AND severity=ERROR" \
  --project=$PROJECT_ID \
  --limit=10
```

### Métricas

```bash
# Tempo de execução
bq query --use_legacy_sql=false <<EOF
SELECT
  job_id,
  creation_time,
  DATE(creation_time) as date,
  TIMESTAMP_DIFF(end_time, creation_time, SECOND) as duration_sec,
  total_bytes_processed,
  total_bytes_billed
FROM \`region-us\`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
AND job_id LIKE "%tse%"
ORDER BY creation_time DESC
LIMIT 10;
EOF
```

### Alertas (Budget)

```bash
# Terraform already setup:
# 50%  = $25/mês → email
# 90%  = $45/mês → email
# 100% = $50/mês → email + bloqueia recursos
```

---

## 🔧 Configuração — O que fazer para ativar agendamento

### Opção 1: Cloud Scheduler (Recomendado)

```bash
# 1. Criar Cloud Scheduler job
gcloud scheduler jobs create pubsub spepe-daily-tse \
  --schedule="0 2 * * *" \
  --topic=tse-ingest-trigger \
  --message-body='{"uf":"SP","year":2022}' \
  --project=$PROJECT_ID \
  --location=$REGION

# 2. Criar Eventarc trigger (Pub/Sub → Job)
gcloud eventarc triggers create spepe-tse-ingest-trigger \
  --event-filters="type=google.cloud.pubsub.topic.v1.messagePublished" \
  --event-filters="resourceName=projects/$PROJECT_ID/topics/tse-ingest-trigger" \
  --destination-run-job=spepe-tse-ingest \
  --destination-run-region=$REGION \
  --service-account=$SA_EMAIL

# 3. Repetir para outros jobs
# - ibge_sync: 0 3 * * *  (3am, após TSE)
# - silver_transform: 0 4 * * *  (4am, após IBGE)
# - gold_build: 0 5 * * *  (5am, após Silver)
```

### Opção 2: Airflow (Alternativa)

Se preferir orquestração mais sofisticada:

```bash
# Usar mlops/vertex_pipeline.py que já tem estrutura KFP
# Pode ser convertido para Airflow DAG facilmente
```

### Opção 3: Manual (Atual)

```bash
# Script local
#!/bin/bash
for uf in SP RJ MG BA RS; do
  gcloud run jobs execute spepe-tse-ingest --args="--uf,$uf"
done

# Rodar via cron (local ou Cloud Shell)
0 2 * * * /home/user/ingest-all-ufs.sh
```

---

## 📋 Checklist — Para Ativar Pipeline Completo

**Fase 1 (v1.0.0) — Manual:**
- [ ] TSE data baixado (SP 2022)
- [ ] IBGE data sincronizado (SP)
- [ ] Silver transform rodado (DQ check passed)
- [ ] Gold build rodado
- [ ] BigQuery tables validadas

**Fase 1.5 — Agendamento Básico:**
- [ ] Cloud Scheduler criado (gcloud scheduler jobs create)
- [ ] Pub/Sub topics criados
- [ ] Eventarc triggers criados
- [ ] Teste completo ponta-a-ponta (2h de execução)

**Fase 2+ — Orquestração Avançada:**
- [ ] Airflow DAGs vs KFP pipelines
- [ ] MLOps pipeline (drift detection)
- [ ] Auto-retrain (Eventarc → MLOps)
- [ ] Monitoramento em tempo real

---

## 🚨 Frequência de Atualização — Recomendada

| Fonte | Frequência | Motivo |
|-------|-----------|--------|
| TSE | Uma vez por eleição (2022 snapshot) | Dados históricos, não muda |
| IBGE | Anual (Censo 2010, SIDRA mensal) | Indicadores estruturais mudam lentamente |
| Pesquisas | Semanal (durante campanha) | Intenção de voto muda rapidamente |
| Social (Fase 2) | Diário/Hora (streaming) | Narrativas mudam em tempo real |
| DATASUS (Fase 2) | Mensal | Dados de saúde pública |

**Para Fase 1 (eleições passadas):**
- TSE 2014, 2018, 2022: uma vez
- IBGE: uma vez (dados estáticos)

---

## Exemplo Completo — terraform apply

Para ativar agendamento após `terraform apply`:

```bash
cd infra/terraform

# 1. Adicionar ao Terraform (criar arquivo cloud_scheduler.tf)
cat > cloud_scheduler.tf <<'EOF'
resource "google_cloud_scheduler_job" "daily_ingest" {
  for_each = {
    tse_ingest    = { time = "0 2 * * *", uf = "SP", year = "2022" }
    ibge_sync     = { time = "0 3 * * *", uf = "SP", year = "2022" }
    silver_transform = { time = "0 4 * * *", uf = "SP", year = "2022" }
    gold_build    = { time = "0 5 * * *", uf = "", year = "" }
  }
  
  name     = "spepe-${each.key}"
  schedule = each.value.time
  timezone = "America/Sao_Paulo"
  region   = var.region
  
  pubsub_target {
    topic_name = google_pubsub_topic.ingest_trigger.id
    data       = base64encode(jsonencode({
      job_name = each.key
      uf       = each.value.uf
      year     = each.value.year
    }))
  }
}
EOF

# 2. Apply
terraform apply

# 3. Verificar
gcloud scheduler jobs list --location=$REGION
```

---

**Status:** v1.0.0 — Manual only (recomenda-se implementar agendamento antes de produção)

**Próximo:** Implementar Cloud Scheduler + Eventarc trigger (1-2 horas de setup)
