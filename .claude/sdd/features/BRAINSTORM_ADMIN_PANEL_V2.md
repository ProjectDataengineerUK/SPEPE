# BRAINSTORM — Admin Panel v2: Sentinel Real-Time + Monitoramento Completo

**Data:** 2026-05-08
**Feature:** ADMIN_PANEL_V2
**Status:** Aprovado para /define

---

## Problema

O painel admin do SPEPE não exibe dados reais e não atualiza em tempo real:
- **Sentinel:** tabela `spepe_mlops.sentinel_state` existe mas tem 0 linhas — nada a popula
- **Usuários/Jobs:** fallback in-memory (`_USER_STORE`) quando Firestore indisponível
- **Cobertura incompleta:** apenas stub — 20 jobs, 17 tabelas Gold, Silver, 22 views, 10 agentes, MLOps, custos e maturidade são invisíveis no painel
- **Sem tempo real:** admin precisa fazer refresh manual para ver mudanças de estado

---

## Discovery

**Q1: Prioridade?**
→ Monitoramento completo de todos os recursos + tempo real — tudo em uma iteração

**Q2: Fonte dos dados do sentinel?**
→ Admin API consulta BQ diretamente + Cloud Run API — sem job intermediário, dados sempre frescos

**Q3: Como determinar health status?**
→ 🟢 Verde: freshness < 24h, DQ > 0.95, Brier < 0.20
→ 🟡 Amarelo: freshness 24–72h, DQ 0.90–0.95, falha recente em job
→ 🔴 Vermelho: freshness > 72h, tabela vazia, job nunca executado

**Q4: Mecanismo de tempo real?**
→ **SSE (Server-Sent Events)** — mais eficiente, menor custo, sem overhead de WebSocket, nativo no Cloud Run sem config extra

---

## Abordagem: Query BQ direto + SSE streaming

**Como funciona:**
```
GET /admin/api/sentinel/stream  (SSE — text/event-stream)
  → Abre conexão persistente com o browser
  → A cada evento relevante, emite JSON:
      - Cloud Run Job mudou de estado → push imediato via Pub/Sub listener
      - Tabelas BQ (freshness/row_count) → polling 15s (BQ não tem streaming de metadata)
      - Agentes LLM → emite métricas a cada execução concluída
      - Custos → polling 60s via BQ INFORMATION_SCHEMA.JOBS_BY_PROJECT
```

**Pros:** Push nativo, sem cache stale, sem polling agressivo no browser, sem Redis, sem infra extra
**Contras:** Conexão persistente por tab aberta — aceitável para painel admin (uso baixo)

---

## Escopo Completo

### 1. DataOps — Jobs
- 20 Cloud Run Jobs: status (ok/warn/error), último execution, jobs que nunca rodaram
- Fonte: `google-cloud-run` SDK → `executions.list()` por job

### 2. DataOps — Gold Tables (17)
- Row count real, freshness_hours, DQ score por tabela
- Fonte: `spepe-prod.spepe_gold.INFORMATION_SCHEMA.TABLE_STORAGE`

### 3. DataOps — Silver Tables
- Row count e freshness das tabelas Silver (spepe_silver.*)
- Fonte: `spepe-prod.spepe_silver.INFORMATION_SCHEMA.TABLE_STORAGE`

### 4. DataOps — Semantic Views (22)
- Existência no BQ, freshness (via TABLE_STORAGE de views materializadas)
- Fonte: `INFORMATION_SCHEMA.VIEWS` + query de validação por view

### 5. Validação de Dados
- DQ score por tabela Gold e Silver: completude, unicidade, schema drift, freshness
- Alertas quando DQ score < 0.90 com histórico de trend 7 dias
- Fonte: execução de checks via `dataops/silver_transformer.py` (campo `dq_score`) + INFORMATION_SCHEMA

### 6. MLOps — Modelo
- Brier score histórico, drift JS divergence, bias por UF, calibração
- Fonte: `spepe_mlops.model_evaluations` + `spepe_mlops.bias_metrics`

### 7. LLMOps — 10 Agentes
- Última execução, erros, latência p50/p99, custo por agente (tokens × preço)
- Fonte: Firestore `spepe_sessions` + audit_hook logs

### 8. Custos GCP
- BQ scan cost (bytes processados × $5/TB)
- Cloud Run execution cost (vCPU-seconds × memory-seconds)
- LLM token cost (Claude Sonnet/Haiku via Anthropic API + Gemini via Vertex)
- Fonte: `INFORMATION_SCHEMA.JOBS_BY_PROJECT` + Cloud Run metrics

### 9. Score de Maturidade
- **DataOps Score (0-100):** cobertura de ingestão, DQ, freshness, lineage
- **MLOps Score (0-100):** pipeline automatizado, drift monitor, canary, eval
- **LLMOps Score (0-100):** eval dataset, guardrails, cost tracking, latência p99
- Radar chart comparando 3 dimensões

---

## Regras de Status

| Condição | Status | Cor |
|----------|--------|-----|
| row_count = 0 | `"error"` | 🔴 |
| freshness_hours > 72 | `"error"` | 🔴 |
| freshness_hours 24–72 | `"warn"` | 🟡 |
| DQ score < 0.90 | `"warn"` | 🟡 |
| freshness_hours < 24 e row_count > 0 | `"ok"` | 🟢 |
| Job nunca executado | `"error"` | 🔴 |
| Job último execution falhou | `"warn"` | 🟡 |
| Job último execution ok < 48h | `"ok"` | 🟢 |

---

## YAGNI — O que foi removido

| Feature | Motivo da remoção |
|---------|------------------|
| spepe-sentinel-populate job | Overkill — BQ direto resolve |
| Terraform para sentinel_state populate | Não necessário com query direta |
| WebSocket bidirecional | SSE unidirecional é suficiente e mais barato |
| Redis cache | SSE elimina necessidade de cache — dados sempre frescos via stream |
| Cache in-memory 5min | Incompatível com real-time |
| Cloud Monitoring / Stackdriver API | INFORMATION_SCHEMA é suficiente e sem custo extra |

---

## Requisitos

1. `GET /admin/api/sentinel/stream` abre SSE e emite eventos em tempo real
2. Eventos: job_status_changed, table_freshness_updated, agent_execution_done, cost_updated
3. Browser atualiza painel sem refresh manual em até 5s após mudança de estado
4. Fallback: se SSE falhar → polling GET /admin/api/sentinel/status a cada 30s
5. Cobertura: 20 jobs + 17 Gold tables + Silver tables + 22 views + 10 agentes + MLOps + custos + maturidade
6. Score de maturidade calculado dinamicamente com base nos dados coletados
7. Latência máxima evento → UI: 5 segundos

---

## Próximo Passo

```bash
/design .claude/sdd/features/DEFINE_ADMIN_PANEL_V2.md
```
