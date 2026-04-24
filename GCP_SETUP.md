# SPEPE — Guia de Configuração GCP

Passo a passo para provisionar infraestrutura no Google Cloud Platform (southamerica-east1, LGPD-compliant).

---

## Fase 0: Pré-requisitos

### Ferramentas necessárias
```bash
# gcloud CLI
curl https://sdk.cloud.google.com | bash
exec -l $SHELL

# Terraform >= 1.5
brew install terraform  # macOS
# ou choco install terraform  # Windows

# jq (para parsing JSON)
brew install jq
```

### Autenticação inicial
```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

---

## Fase 1: Setup Inicial (30 min)

### 1.1 Criar projeto GCP (se não existir)
```bash
export PROJECT_ID="spepe-dev"  # ou seu projeto
export REGION="southamerica-east1"
export VERTEX_REGION="us-central1"  # Vertex AI não existe em SA-east1

gcloud projects create $PROJECT_ID --name="SPEPE Electoral Analytics"
gcloud config set project $PROJECT_ID
```

### 1.2 Habilitar APIs necessárias
```bash
gcloud services enable \
  compute.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  jobs-api.cloudrun.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iap.googleapis.com \
  pubsub.googleapis.com \
  eventarc.googleapis.com \
  aiplatform.googleapis.com \
  cloudkms.googleapis.com \
  firestore.googleapis.com \
  monitoring.googleapis.com \
  logging.googleapis.com
```

### 1.3 Criar bucket Terraform state (ANTES do terraform init)
```bash
gsutil mb -p $PROJECT_ID -l $REGION \
  gs://${PROJECT_ID}-terraform-state

# Habilitar versionamento
gsutil versioning set on gs://${PROJECT_ID}-terraform-state
```

---

## Fase 2: Service Accounts & IAM (20 min)

### 2.1 Criar Service Account principal
```bash
gcloud iam service-accounts create spepe-app \
  --display-name="SPEPE Application SA" \
  --description="Principal service account para Cloud Run, Jobs, BigQuery"

# Note o email gerado (você vai precisar)
SA_EMAIL=$(gcloud iam service-accounts describe spepe-app --format='value(email)')
echo "Service Account: $SA_EMAIL"
```

### 2.2 Conceder roles necessários ao SA
```bash
# BigQuery
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/bigquery.admin"

# GCS (Bronze layer)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.admin"

# Cloud Run
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.admin"

# Cloud Run Jobs
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.invoker"

# Cloud Tasks (para async jobs)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/cloudtasks.taskRunner"

# Vertex AI
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/aiplatform.user"

# Secret Manager (vai ser refinado no Terraform)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"

# Pub/Sub
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/pubsub.editor"

# Firestore
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/datastore.user"

# Logs
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/logging.logWriter"
```

### 2.3 Criar Workload Identity Pool para GitHub Actions (CI/CD)
```bash
# Criar WIF pool
POOL_ID="github-oidc"
gcloud iam workload-identity-pools create $POOL_ID \
  --project=$PROJECT_ID \
  --location=global \
  --display-name="GitHub Actions OIDC"

# Criar WIF provider
PROVIDER_ID="github"
gcloud iam workload-identity-pools providers create-oidc $PROVIDER_ID \
  --project=$PROJECT_ID \
  --location=global \
  --workload-identity-pool=$POOL_ID \
  --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.aud=assertion.aud" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-condition="assertion.aud == '${PROJECT_ID}'"

# Obter WIF resource name
WIF_POOL_RESOURCE=$(gcloud iam workload-identity-pools describe $POOL_ID \
  --project=$PROJECT_ID \
  --location=global \
  --format='value(name)')

echo "WIF Pool Resource: $WIF_POOL_RESOURCE"

# Bind WIF ao SA (para CI/CD)
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --project=$PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/$WIF_POOL_RESOURCE/attribute.repository/SEU_USUARIO/SPEPE"
```

---

## Fase 3: Artifact Registry (Docker) (10 min)

### 3.1 Criar repositório Docker
```bash
gcloud artifacts repositories create spepe \
  --project=$PROJECT_ID \
  --repository-format=docker \
  --location=$REGION \
  --description="SPEPE application container images"

# Configure Docker auth
gcloud auth configure-docker ${REGION}-docker.pkg.dev
```

### 3.2 Build e push da imagem (local)
```bash
# Na raiz do repo
docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/spepe/app:v1.0.0 .

docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/spepe/app:v1.0.0

# Guardar a imagem URI para o Terraform
export APP_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/spepe/app:v1.0.0"
echo "APP_IMAGE=$APP_IMAGE"
```

---

## Fase 4: Terraform Deploy (45 min)

### 4.1 Inicializar Terraform
```bash
cd infra/terraform

terraform init \
  -backend-config="bucket=${PROJECT_ID}-terraform-state" \
  -backend-config="prefix=spepe"
```

### 4.2 Preparar variáveis
```bash
# Criar arquivo terraform.tfvars (ou passar via -var)
cat > terraform.tfvars <<EOF
project_id       = "$PROJECT_ID"
region           = "$REGION"
vertex_region    = "$VERTEX_REGION"
environment      = "dev"  # ou "staging", "prod"
admin_email      = "SEU_EMAIL@example.com"
app_image        = "$APP_IMAGE"
wif_pool_id      = "$POOL_ID"
github_repo      = "SEU_USUARIO/SPEPE"
budget_alert_usd = 50
EOF
```

### 4.3 Plan & Apply
```bash
# Ver o que vai ser criado
terraform plan

# Aplicar
terraform apply

# Guardar outputs
terraform output > outputs.json
```

---

## Fase 5: Secrets Manager — Adicionar Credenciais (15 min)

### 5.1 ANTHROPIC_API_KEY

**Onde obter:**
1. Acesse https://console.anthropic.com/account/keys
2. Crie ou copie sua chave existente
3. Revogue a chave antiga se necessário

**Adicionar ao Secret Manager:**
```bash
echo -n "sk-ant-YOUR_KEY_HERE" | \
  gcloud secrets versions add ANTHROPIC_API_KEY \
  --data-file=- \
  --project=$PROJECT_ID
```

### 5.2 META_APP_TOKEN (Opcional — Fase 2)

**Onde obter:**
1. Meta Business Suite: https://business.facebook.com/
2. Configurações → Aplicativos → Seu App
3. Gerar Token de Acesso Permanente

**Adicionar:**
```bash
echo -n "YOUR_META_TOKEN_HERE" | \
  gcloud secrets versions add META_APP_TOKEN \
  --data-file=- \
  --project=$PROJECT_ID
```

### 5.3 YOUTUBE_API_KEY (Opcional — Fase 2)

**Onde obter:**
1. Google Cloud Console → APIs & Services → Credentials
2. Criar API key (YouTube Data API v3)

**Adicionar:**
```bash
echo -n "YOUR_YOUTUBE_API_KEY" | \
  gcloud secrets versions add YOUTUBE_API_KEY \
  --data-file=- \
  --project=$PROJECT_ID
```

### 5.4 Verificar secrets criados
```bash
gcloud secrets list --project=$PROJECT_ID
```

---

## Fase 6: Configurar Cloud Run (10 min)

### 6.1 Deploy Chainlit UI
```bash
# Terraform já criou o serviço, mas você pode redeploy:
gcloud run deploy spepe \
  --image=$APP_IMAGE \
  --project=$PROJECT_ID \
  --region=$REGION \
  --service-account=$SA_EMAIL \
  --set-env-vars="USE_BIGQUERY=true,GCP_PROJECT_ID=$PROJECT_ID" \
  --memory=2Gi \
  --cpu=2 \
  --timeout=3600 \
  --max-instances=10 \
  --allow-unauthenticated  # dev only; use IAP em prod
```

### 6.2 Obter URL
```bash
gcloud run services describe spepe \
  --project=$PROJECT_ID \
  --region=$REGION \
  --format='value(status.url)'
```

---

## Fase 7: Cloud Run Jobs (TSE/IBGE Ingest) (10 min)

### 7.1 Jobs criados pelo Terraform
- `tse-ingest-job` — Baixa dados TSE
- `ibge-sync-job` — Sincroniza IBGE
- `silver-transform-job` — Bronze → Silver
- `gold-build-job` — Silver → Gold

### 7.2 Executar um job manualmente
```bash
gcloud run jobs execute tse-ingest-job \
  --project=$PROJECT_ID \
  --region=$REGION

# Ver logs
gcloud run jobs log tse-ingest-job \
  --project=$PROJECT_ID \
  --region=$REGION
```

### 7.3 Agendar jobs (Eventarc / Cloud Scheduler)
```bash
# Terraform já criou, mas você pode customizar:
gcloud scheduler jobs create pubsub daily-tse-ingest \
  --schedule="0 2 * * *" \
  --topic=tse-ingest-trigger \
  --message-body="{}" \
  --project=$PROJECT_ID \
  --location=$REGION
```

---

## Fase 8: BigQuery Datasets (5 min)

### 8.1 Verificar datasets criados
```bash
bq ls --project_id=$PROJECT_ID

# Esperado:
# spepe_silver  (staging tables, TTL 90 days)
# spepe_gold    (production tables, partitioned)
# spepe_mlops   (model artifacts, predictions)
```

### 8.2 Criar sample table (teste)
```bash
bq load \
  --autodetect \
  --source_format=PARQUET \
  $PROJECT_ID:spepe_silver.tse_sp_2022 \
  data/silver/tse_sp_2022.parquet
```

---

## Fase 9: CI/CD — GitHub Secrets (5 min)

### 9.1 Adicionar secret no GitHub
```bash
# Seu repo → Settings → Secrets and variables → Actions

# Secrets necessários:
# GCP_PROJECT_ID               = seu project ID
# GCP_WIF_POOL_LOCATION        = "global"
# GCP_WIF_POOL_ID              = "github-oidc"
# GCP_WIF_PROVIDER_ID          = "github"
# GCP_SERVICE_ACCOUNT          = $SA_EMAIL
# ANTHROPIC_API_KEY_TEST       = chave de teste (cópia, não produção)
```

### 9.2 Testar CI pipeline
```bash
# Push para branch → PR automaticamente roda:
# 1. Lint (ruff)
# 2. Tests (pytest)
# 3. LLM eval (eval_runner.py)
# 4. Secret scan (TruffleHog)
```

---

## Fase 10: Monitoramento & Alertas (10 min)

### 10.1 Budget alerts
```bash
# Terraform já criou, mas verificar:
gcloud billing budgets list --billing-account=YOUR_BILLING_ACCOUNT

# Alert em 50%, 90%, 100% de $50/mês (dev)
```

### 10.2 Logs e métricas
```bash
# Cloud Logging
gcloud logging read "resource.type=cloud_run_job AND severity=ERROR" \
  --project=$PROJECT_ID \
  --limit=10

# Métricas BigQuery
bq query --use_legacy_sql=false <<EOF
SELECT
  creation_time,
  project_id,
  dataset_id,
  table_id,
  rows_loaded,
  bytes_loaded
FROM \`region-us\`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
LIMIT 10;
EOF
```

---

## Referência Rápida — Variáveis de Ambiente

Para .env local (dev):
```bash
GCP_PROJECT_ID=spepe-dev
USE_BIGQUERY=false  # Use local parquet first
DATA_DIR=data/
ANTHROPIC_API_KEY=sk-ant-...  # Sua chave
```

Para Cloud Run (prod):
```bash
GCP_PROJECT_ID=spepe-dev
USE_BIGQUERY=true
BIGQUERY_DATASET_SILVER=spepe_silver
BIGQUERY_DATASET_GOLD=spepe_gold
BIGQUERY_DATASET_MLOPS=spepe_mlops
# Secrets vêm de Secret Manager, não ENV
```

---

## Troubleshooting

### Erro: "Quota exceeded for quota metric"
→ Aumentar quotas em Quotas & System Limits

### Erro: "Permission denied on secret"
→ Verificar IAM binding do SA:
```bash
gcloud secrets get-iam-policy ANTHROPIC_API_KEY \
  --project=$PROJECT_ID
```

### BigQuery conecta mas Terraform falha
→ Verificar credentials e `gcloud auth application-default login`

### Cloud Run erro "Cannot connect to backend"
→ Verificar VPC/firewall e roles do SA

---

## Checklist Final

- [ ] APIs habilitadas
- [ ] Service Account criado + roles atribuídos
- [ ] Terraform state bucket criado
- [ ] Terraform apply sem erros
- [ ] Secrets adicionados ao Secret Manager
- [ ] Docker image builded e pushed
- [ ] Cloud Run deployado
- [ ] BigQuery datasets criados
- [ ] GitHub secrets configurados
- [ ] CI/CD workflow testado
- [ ] Monitoramento ativo

---

**Tempo total estimado:** 3-4 horas (primeira vez)

**Próximas etapas após v1.0.0:**
- Configurar IAP para acesso autenticado (prod)
- Implementar auto-retrain loop (Fase 2)
- Habilitar social data ingest (Fase 2)
