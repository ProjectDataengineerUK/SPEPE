# Credenciais Necessárias — SPEPE v1.0.0

Checklist de credenciais e where/how para obtê-las.

---

## 🔴 OBRIGATÓRIAS (Fase 1)

### 1. ANTHROPIC_API_KEY
**O que é:** Chave de API para acessar Claude via Anthropic API

**Onde obter:**
- https://console.anthropic.com/account/keys
- Fazer login com conta Anthropic
- Gerar nova chave ou copiar existente

**Como usar:**
```bash
# Desenvolvimento (local .env)
echo "ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE" >> .env

# Produção (GCP Secret Manager)
echo -n "sk-ant-YOUR_KEY_HERE" | \
  gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-

# Testando
python -c "from anthropic import Anthropic; c = Anthropic(); print(c.models.list())"
```

**Permissões necessárias:**
- Modelo: claude-sonnet-4-6, claude-opus-4-7, claude-haiku-4-5
- Taxa: $5 USD/mês incluído no plan

**Segurança:**
- ❌ NUNCA commitar em .env
- ✅ Usar `gcloud secrets versions add` para prod
- ✅ Rotacionar chaves a cada 90 dias
- ⚠️ Revogar chaves antigas em console.anthropic.com

---

### 2. GCP_PROJECT_ID
**O que é:** ID do projeto Google Cloud

**Onde obter:**
1. Google Cloud Console: https://console.cloud.google.com
2. Selecionar seu projeto
3. Copiar ID na barra superior

**Formato:** `spepe-dev`, `spepe-staging`, `spepe-prod`

**Como usar:**
```bash
# Desenvolvimento
export GCP_PROJECT_ID="spepe-dev"

# Verificar
gcloud config get-value project
```

---

## 🟠 FORTEMENTE RECOMENDADAS (Fase 1 Production)

### 3. GCP Service Account Key (SA)
**O que é:** Credencial para Cloud Run, Jobs, BigQuery

**Onde obter:**
```bash
# Criar via gcloud (no GCP_SETUP.md Fase 2)
gcloud iam service-accounts create spepe-app

# Ou download JSON (menos recomendado)
gcloud iam service-accounts keys create spepe-app.json \
  --iam-account=spepe-app@${PROJECT_ID}.iam.gserviceaccount.com
```

**Permissões necessárias:**
- BigQuery Admin
- Storage Admin
- Cloud Run Admin
- Vertex AI User
- Secret Manager Secret Accessor

**Como usar:**
```bash
# CI/CD (GitHub Actions) — Workload Identity Federation (recomendado)
# Ver GCP_SETUP.md Fase 2.3

# Desenvolvimento local
export GOOGLE_APPLICATION_CREDENTIALS="./spepe-app.json"
gcloud auth activate-service-account --key-file=spepe-app.json
```

**Segurança:**
- ❌ NUNCA commitar .json
- ✅ Usar Workload Identity Federation em prod
- ⚠️ Rotacionar chaves a cada 90 dias

---

## 🟡 OPCIONAIS (Fase 2+)

### 4. META_APP_TOKEN
**O que é:** Token de acesso ao Facebook Ad Library API

**Quando usar:** Fase 2 (social data ingest)

**Onde obter:**
1. Facebook Business Suite: https://business.facebook.com
2. Settings → Apps and Assets → Apps
3. Generate Access Token (Permanent)
4. Selecionar permissões: `ads_read`

**Como usar:**
```bash
echo -n "YOUR_META_TOKEN" | \
  gcloud secrets versions add META_APP_TOKEN --data-file=-

# Usar em dataops/clients/digital_client.py
```

**Permissões necessárias:**
- `ads_read` — Ler dados de anúncios
- `business_management` — Acessar business account

---

### 5. YOUTUBE_API_KEY
**O que é:** Chave para YouTube Data API v3

**Quando usar:** Fase 2 (social data ingest)

**Onde obter:**
1. Google Cloud Console → APIs & Services → Credentials
2. Create Credentials → API Key
3. Habilitar YouTube Data API v3
4. Copiar chave

**Como usar:**
```bash
echo -n "YOUR_YOUTUBE_API_KEY" | \
  gcloud secrets versions add YOUTUBE_API_KEY --data-file=-

# Usar em dataops/clients/digital_client.py
```

**Limites:**
- 10,000 quotas/dia (free tier)
- ~500 candidatos × dias = máximo 20 dias de histórico

---

### 6. DATASUS_AUTH (Futuro)
**O que é:** Autenticação para DATASUS API

**Quando usar:** Fase 2 (dados de saúde)

**Status:** Pendente (DATASUS pode não exigir auth)

---

## 🟢 INFRAESTRUTURA GCP (auto-gerenciado)

Essas credenciais são geradas automaticamente pelo Terraform:

- ✅ **BigQuery datasets** (spepe_silver, spepe_gold, spepe_mlops)
- ✅ **GCS buckets** (spepe_data)
- ✅ **Cloud Run service account**
- ✅ **Cloud Run Jobs SAs**
- ✅ **Workload Identity Pool** (GitHub Actions)

---

## Checklist — O que você precisa fazer

### Antes de terraform apply
- [ ] Ter GCP_PROJECT_ID
- [ ] Ter conta Google Cloud com billing ativo
- [ ] Ter ANTHROPIC_API_KEY pronta
- [ ] Ter gcloud CLI instalado e autenticado

### Depois de terraform apply
- [ ] Adicionar ANTHROPIC_API_KEY ao Secret Manager
- [ ] Adicionar META_APP_TOKEN (opcional, Fase 2)
- [ ] Adicionar YOUTUBE_API_KEY (opcional, Fase 2)
- [ ] Verificar que Cloud Run foi deployed com sucesso
- [ ] Configurar GitHub secrets para CI/CD

### Antes de produção
- [ ] Rotacionar ANTHROPIC_API_KEY a cada 90 dias
- [ ] Habilitar IAP (Identity-Aware Proxy)
- [ ] Configurar budget alerts
- [ ] Monitorar logs e métricas
- [ ] Backup automático de BigQuery (via Terraform)

---

## Referência Rápida — Onde guardar

| Credencial | Desenvolvimento | Produção |
|-----------|-----------------|----------|
| ANTHROPIC_API_KEY | `.env` (local) | `gcloud secrets` |
| GCP_PROJECT_ID | `.env` | CI/CD secrets |
| Service Account | `gcloud auth` | Workload Identity |
| META_APP_TOKEN | `.env` (se teste) | `gcloud secrets` |
| YOUTUBE_API_KEY | `.env` (se teste) | `gcloud secrets` |

---

## Segurança — Best Practices

1. **Nunca commitar credenciais**
   ```bash
   # ✅ Bom
   export ANTHROPIC_API_KEY=$(gcloud secrets versions access latest --secret=ANTHROPIC_API_KEY)
   
   # ❌ Ruim
   export ANTHROPIC_API_KEY="sk-ant-xyz"  # em .env commitado
   ```

2. **Rotacionar periodicamente**
   ```bash
   # Criar nova chave em console.anthropic.com
   # Atualizar em Secret Manager
   # Esperar 7 dias
   # Revogar chave antiga
   ```

3. **Usar chaves específicas por ambiente**
   ```bash
   ANTHROPIC_API_KEY_DEV=sk-ant-dev-...     # rate limit baixo
   ANTHROPIC_API_KEY_TEST=sk-ant-test-...   # para CI
   ANTHROPIC_API_KEY_PROD=sk-ant-prod-...   # production
   ```

4. **Auditar acesso**
   ```bash
   gcloud secrets versions list ANTHROPIC_API_KEY
   gcloud logging read "protoPayload.methodName=google.iam.admin.v1.GetServiceAccountKey"
   ```

---

## Troubleshooting

### "Authentication failed" ao fazer deploy
```bash
# Verificar credencial padrão
gcloud auth list
gcloud auth activate-service-account --key-file=spepe-app.json

# Ou usar application-default
gcloud auth application-default login
```

### "Permission denied: Secret Manager"
```bash
# Verificar IAM binding
gcloud secrets get-iam-policy ANTHROPIC_API_KEY

# Adicionar permissão
gcloud secrets add-iam-policy-binding ANTHROPIC_API_KEY \
  --member=serviceAccount:spepe-app@${PROJECT_ID}.iam.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
```

### "Invalid API key" (Anthropic)
```bash
# Verificar chave
echo $ANTHROPIC_API_KEY

# Testar
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"claude-3-sonnet-20240229","max_tokens":100,"messages":[{"role":"user","content":"hi"}]}'
```

---

**Última atualização:** 2026-04-24  
**Status:** v1.0.0 — Fase 1 validation complete
