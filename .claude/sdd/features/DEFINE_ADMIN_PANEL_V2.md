# DEFINE: Admin Panel v2 — Sentinel Real-Time + Monitoramento Completo

> Transformar o painel admin em um centro de controle com monitoramento em tempo real (SSE) de todos os recursos SPEPE: 20 jobs, 17 Gold tables, Silver tables, 22 views semânticas, 10 agentes LLM, métricas MLOps, custos GCP e score de maturidade DataOps/MLOps/LLMOps.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | ADMIN_PANEL_V2 |
| **Date** | 2026-05-08 |
| **Author** | define-agent |
| **Status** | Ready for Design |
| **Version** | 2.0 |
| **Clarity Score** | 15/15 |

---

## Problem Statement

O painel admin do SPEPE exibe dados fictícios (stub) no Sentinel e não atualiza em tempo real. Administradores não conseguem monitorar o estado real do sistema — os 20 Cloud Run Jobs, 17 tabelas Gold, Silver, 22 views semânticas, 10 agentes LLM, métricas de modelo, custos e score de maturidade são completamente invisíveis. O admin precisa de um refresh manual e ainda assim só vê stubs.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| Admin SPEPE | Operador do sistema | Não sabe se jobs falharam, dados estão frescos, modelo degradou ou custo disparou |
| Engenheiro de dados | DataOps/MLOps | Não consegue detectar falhas, drift ou dados desatualizados pelo painel |

---

## Goals

| Priority | Goal |
|----------|------|
| **MUST** | SSE endpoint `/admin/api/sentinel/stream` emite eventos em tempo real |
| **MUST** | 20 Cloud Run Jobs com status real (ok/warn/error), último execution, jobs nunca executados |
| **MUST** | 17 Gold tables com row_count real, freshness_hours e status calculado |
| **MUST** | Silver tables com row_count e freshness |
| **MUST** | 22 Semantic Views com existência confirmada no BQ |
| **MUST** | 10 Agentes LLM com última execução, erros e latência |
| **MUST** | Fallback gracioso para polling 30s se SSE falhar |
| **MUST** | Aba Validação de Dados: DQ score (completude, unicidade, schema drift) por tabela Gold e Silver |
| **MUST** | Alertas de DQ score abaixo de threshold (< 0.90) com histórico 7 dias |
| **SHOULD** | Métricas MLOps: Brier score, drift JS, bias por UF |
| **SHOULD** | Custos GCP: BQ scan, Cloud Run execution, LLM tokens |
| **SHOULD** | Score de maturidade DataOps/MLOps/LLMOps (0-100) com radar chart |
| **COULD** | Aba Arquitetura: health map visual dos 20 jobs + 10 agentes + 22 views |

---

## Seções do Painel (Novas Abas)

| Aba | Conteúdo |
|-----|----------|
| **Sentinel** (existente, expandida) | Status geral em tempo real — todos os recursos em verde/amarelo/vermelho |
| **DataOps** | 20 jobs, Gold tables, Silver tables, 22 views — freshness e status por camada |
| **Validação de Dados** | DQ score por tabela (completude, unicidade, schema drift), alertas de threshold, histórico 7 dias |
| **Validação de Modelo** | Brier score histórico (curva temporal), calibração, accuracy por cargo, drift JS divergence, bias por UF |
| **LLMOps** | 10 agentes — latência p50/p99, custo por agente, erros recentes |
| **Custos** | BQ scan cost, Cloud Run cost, LLM token cost — por dia/semana/mês |
| **Maturidade** | Radar chart DataOps/MLOps/LLMOps score (0-100) com benchmarks |
| **Arquitetura** | Health map visual: Bronze→Silver→Gold→Agents — status por nó |

---

## Success Criteria

- [ ] SSE: browser recebe evento em ≤ 5s após mudança de estado de job
- [ ] 20 Cloud Run Jobs listados com status do último execution real
- [ ] Jobs nunca executados aparecem com status `"error"` e `last_run = null`
- [ ] 17 Gold tables com `row_count` real e `freshness_hours` calculado
- [ ] Tabelas Gold com 0 rows → status `"error"`; freshness > 72h → `"warn"`
- [ ] Silver tables aparecem na aba DataOps com freshness real
- [ ] 22 views semânticas listadas com existência confirmada no BQ
- [ ] 10 agentes LLM com latência p50/p99 e custo estimado por execução
- [ ] Aba MLOps exibe Brier score e drift do último modelo treinado
- [ ] Aba Custos exibe custo diário de BQ + Cloud Run + LLM tokens
- [ ] Score de maturidade calculado e exibido no radar chart
- [ ] Fallback: se SSE falhar → polling 30s sem erro visível ao usuário
- [ ] `source: "bigquery"` quando USE_BIGQUERY=true e BQ acessível
- [ ] `source: "stub"` quando BQ inacessível — HTTP 200, sem erro 500
- [ ] Latência máxima da rota status: 10s (timeout BQ)

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | Gold saudável | `fact_municipio_eleicao` tem 2.4M rows, modified < 24h | GET /stream | Evento emite status `"ok"`, freshness_hours < 24, row_count = 2400000 |
| AT-002 | Gold vazia | `fact_social_municipio` tem 0 rows | GET /stream | Evento status `"error"`, row_count = 0, alert_message "tabela vazia" |
| AT-003 | Gold desatualizada | `fact_seguranca_municipio` modified há 80h | GET /stream | status `"warn"`, freshness_hours = 80 |
| AT-004 | BQ indisponível | USE_BIGQUERY=false | GET /stream | Fallback polling, source = "stub", HTTP 200 |
| AT-005 | Job nunca executado | spepe-ibge-sync sem execuções | GET /stream | Job status `"error"`, last_run = null |
| AT-006 | Job com falha | spepe-candidatos-discovery falhou hoje | GET /stream | status `"warn"`, last_status = "EXECUTION_FAILED" |
| AT-007 | Real-time job | spepe-tse-ingest muda de RUNNING → SUCCEEDED | Push SSE | Browser atualiza em ≤ 5s sem refresh manual |
| AT-008 | Silver freshness | spepe_silver.tse_SP_2022 modified < 24h | GET /stream | Silver aparece na aba DataOps com status "ok" |
| AT-009 | View existência | vw_candidato_360 existe no BQ | GET /stream | View listada com status "ok" |
| AT-010 | View ausente | vw_social_crise_detector não existe | GET /stream | View status "error", alert "view não encontrada no BQ" |
| AT-011 | Agente latência | analista executou em 1.8s, custo $0.003 | GET /stream | Agente aparece em LLMOps com p50=1.8s, custo estimado |
| AT-012 | Custo BQ | 500GB processados hoje | Aba Custos | Exibe $2.50 (500GB × $5/TB) |
| AT-013 | Score maturidade | 15/20 jobs rodaram, DQ > 0.95, eval_runner 0.995 | Aba Maturidade | DataOps=78, MLOps=65, LLMOps=82 no radar |

---

## Out of Scope

- Criar novo Cloud Run Job para popular `sentinel_state` (BQ direto resolve)
- WebSocket bidirecional (SSE unidirecional é suficiente)
- Redis para cache (SSE elimina necessidade de cache)
- Cloud Monitoring / Stackdriver API (INFORMATION_SCHEMA suficiente)
- Alertas por e-mail/Slack (monitoramento visual apenas nesta versão)

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Technical | SSE via FastAPI `StreamingResponse` — sem dependência extra | Cloud Run suporta nativamente |
| Technical | BQ metadata (TABLE_STORAGE): polling 15s — BQ não tem streaming de metadata | Aceitável para freshness |
| Technical | Jobs status via Cloud Run SDK `executions.list()` — sem audit log | Requer IAM `run.executions.list` no SA |
| Technical | Manter schema de resposta do sentinel (resource_id, category, status, metrics, alert_message) | Design mapeia BQ → schema |
| Security | Rota protegida por `require_auth` (Google Bearer token) já implementado | Sem mudanças de auth |
| Cost | BQ INFORMATION_SCHEMA.TABLE_STORAGE: sem custo de scan | Metadata table gratuita |

---

## Data Contract — Fontes

| Dimensão | Fonte | Dados |
|----------|-------|-------|
| Gold tables | `spepe-prod.spepe_gold.INFORMATION_SCHEMA.TABLE_STORAGE` | total_rows, last_modified_time |
| Silver tables | `spepe-prod.spepe_silver.INFORMATION_SCHEMA.TABLE_STORAGE` | total_rows, last_modified_time |
| Semantic views | `spepe-prod.spepe_gold.INFORMATION_SCHEMA.VIEWS` | view_definition, existência |
| MLOps modelo | `spepe-prod.spepe_mlops.model_evaluations` | brier_score, accuracy, model_version |
| MLOps bias | `spepe-prod.spepe_mlops.bias_metrics` | sg_uf, bias_score, computed_at |
| Jobs status | Cloud Run SDK `executions.list()` | job_name, last_status, completion_time |
| Agentes LLM | Firestore `spepe_sessions` + audit_hook | latência, tokens, custo, erros |
| Custos BQ | `INFORMATION_SCHEMA.JOBS_BY_PROJECT` | bytes_processed, cost_estimate |
| Custos Cloud Run | Cloud Run metrics API | cpu_seconds, memory_seconds |

### Schema de Resposta SSE (evento)

```json
{
  "event": "table_freshness_updated",
  "resource_id": "dataops:fact_municipio_eleicao",
  "category": "dataops",
  "layer": "gold",
  "status": "ok",
  "metrics": {
    "row_count": 2400000,
    "freshness_hours": 18.5,
    "last_modified": "2026-05-07T23:21:07Z"
  },
  "alert_message": null,
  "timestamp": "2026-05-08T15:00:00Z"
}
```

### Tipos de Eventos SSE

| event | Trigger | Frequência |
|-------|---------|-----------|
| `job_status_changed` | Cloud Run execution muda estado | Imediato via Pub/Sub |
| `table_freshness_updated` | Polling BQ metadata | 15s |
| `agent_execution_done` | Agente LLM conclui chamada | Imediato via hook |
| `cost_updated` | Polling BQ JOBS | 60s |
| `heartbeat` | Keepalive SSE | 30s |

---

## Technical Context

| Aspect | Value |
|--------|-------|
| **Arquivo principal** | `ui/dashboard_api.py` |
| **SSE endpoint novo** | `GET /admin/api/sentinel/stream` |
| **Status endpoint existente** | `GET /admin/api/sentinel/status` (mantido como fallback) |
| **Deployment** | Cloud Run service `spepe-prod` |
| **IaC Impact** | Nenhum — sem novos recursos Terraform |

---

## Assumptions

| ID | Assumption | Validado? |
|----|------------|-----------|
| A-001 | `INFORMATION_SCHEMA.TABLE_STORAGE` sem custo de scan | ✅ |
| A-002 | Cloud Run SDK disponível no ambiente | ✅ |
| A-003 | FastAPI `StreamingResponse` suporta SSE sem biblioteca extra | ✅ |
| A-004 | Firestore `spepe_sessions` contém logs de execução dos agentes | [ ] verificar |
| A-005 | SA tem permissão `run.executions.list` em spepe-prod | [ ] verificar |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-08 | define-agent | Scope inicial Phase 1 |
| 2.0 | 2026-05-08 | iterate | Escopo completo: SSE real-time + 7 abas + custos + maturidade |

---

## Next Step

**Ready for:** `/design .claude/sdd/features/DEFINE_ADMIN_PANEL_V2.md`
