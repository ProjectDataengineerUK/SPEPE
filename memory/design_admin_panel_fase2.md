# Admin Panel Fase 2 — Design Completo (5 Abas)

**Data:** 2026-05-12
**Status:** DESIGN COMPLETO (Não código)
**Complexidade:** Média-Alta
**Estimativa implementação:** 40-50h (código + testes + integração)

---

## Visão Geral

O Admin Panel Fase 2 expande a capacidade de monitoramento e validação do SPEPE. Enquanto Fase 1 (5 abas core) foca em operações diárias e chat, Fase 2 adiciona 5 abas especializadas para análise profunda, alertas inteligentes e validação de dados/modelos.

### Hierarquia de Abas
```
FASE 1 (MVP)                    FASE 2 (OBSERVABILIDADE)
├─ Dashboard                    ├─ Sentinel ⭐
├─ Mapa                         ├─ Arquitetura ⭐
├─ Chat                         ├─ KPIs ⭐
├─ Resultados Eleitorais        ├─ Validação Manual ⭐
└─ Admin (Usuários + Acesso)    └─ Validação do Modelo ⭐
```

---

## 1. ABA: SENTINEL — Monitoramento em Tempo Real

### Propósito
Observabilidade centralizada: custos, saúde dos serviços, alertas e timeline de eventos. Visão executiva em 30 segundos.

### Layout Base (4 seções)

#### 1.1 Header Summary (topo, flex-row)
```
┌─────────────────────────────────────────────────────────────────────┐
│ 🟢 OPERAÇÃO NOMINAL  │  💰 $2,156.43 mês  │  ⚠️  2 ALERTAS  │ Δ 12% ↑ │
│  (9/10 serviços OK)  │  (↑8% vs mês ant.) │  (últimas 24h)  │ (custo)  │
└─────────────────────────────────────────────────────────────────────┘
```

**Componentes:**
- Status badge: cores (🟢 OK, 🟡 WARN, 🔴 BREACH)
- Custo total do mês em USD com Δ trending
- Contagem de alertas activos com link para seção
- Taxa de mudança de custo

#### 1.2 Custos — Cards em Grid (2x3)

**Cards de breakdown:**

| Card | Métrica | Layout | Variação |
|------|---------|--------|----------|
| **LLM Tokens** | Total tokens gastos | Contador grande + sparkline | Δ vs dia anterior |
| **Por Modelo** | Claude+Gemini breakdown | Barra stacked + pie | Δ% por modelo |
| **BigQuery** | Gigabytes scaneados | Contador + trending | Δ vs semana |
| **Cloud Run** | CPU·seg + mem·GB | Contador + trending | Δ vs baseline |
| **GCS** | Tráfego egress + storage | Contador + trending | Δ vs quota |
| **Previsão** | Custo projetado mês | Forecast bar + threshold | Alerta se > budget |

**Exemplo Card:**
```
┌─────────────────────┐
│ LLM TOKENS          │
│ ─────────────────── │
│  125,456 ↑ 8%      │  (trending up)
│                     │
│ Claude Sonnet:      │
│  75,234 (60%)  ███  │
│ Gemini 2.5 Flash:   │
│  32,166 (26%)  ██   │
│ Gemini 2.5 Pro:     │
│  18,056 (14%)  █    │
│                     │
│ Sparkline histórico │
└─────────────────────┘
```

#### 1.3 Status de Serviços (Cards em coluna)

**Health checks por categoria:**

| Serviço | Status | Última check | Latência | Ação |
|---------|--------|--------------|----------|------|
| Chainlit UI | 🟢 OK | 32s atrás | 145ms | - |
| Dashboard API | 🟢 OK | 1m atrás | 89ms | - |
| BigQuery | 🟢 OK | 5m atrás | 2.1s | - |
| Supervisor | 🟡 WARN | 8m atrás | 3.2s | Escalate |
| Vertex AI | 🟢 OK | 15m atrás | 1.5s | - |
| Cloud Run Jobs | 🟢 OK | 22m atrás | - | - |
| Pub/Sub Drift | 🔴 BREACH | 30m atrás | 5.0s | Emergency |
| GCS Sync | 🟢 OK | 45m atrás | 890ms | - |

**Card template:**
```
┌─────────────────────────────────────────────────────┐
│ 🟢 SUPERVISOR  │ Última: 8m atrás  │  Latência: 3.2s  │
│ ─────────────────────────────────────────────────── │
│                                                       │
│ 🟡 Resposta lenta (P99 > 2s) — monitorar            │
│                                                       │
│  [Detalhes]  [Logs]  [Escalate]                     │
└─────────────────────────────────────────────────────┘
```

#### 1.4 Alertas & Timeline (tipo Activity Feed)

**Event stream com filtros:**

```
┌────────────────────────────────────────────────────────┐
│ TIMELINE DE EVENTOS (últimas 24h)                       │
│ ────────────────────────────────────────────────────── │
│                                                         │
│ 14:23  🔴 BREACH   Pub/Sub acumula 5k msgs não proc. │
│        Ação: Reiniciar consumer  [Executar]           │
│                                                         │
│ 13:15  🟡 WARN     BigQuery scan 450GB/dia (vs 300)  │
│        Root: nova feature no modelo                    │
│                                                         │
│ 12:00  🟢 OK       Cloud Run 99.8% uptime            │
│                                                         │
│ 10:45  💰 INFO     Gasto diário: $28.50 (↓12%)       │
│        Otimização de cache acionada                    │
│                                                         │
│ 08:30  🟡 WARN     Supervisor timeout em /prever      │
│        Causa: modelo PyMC lento, tratado              │
│                                                         │
│ ────────────────────────────────────────────────────── │
│ [⏮ Mais antigos] 📊 [Filtrar: status|categoria]       │
└────────────────────────────────────────────────────────┘
```

**Filtros laterais:**
- Status: OK, WARN, BREACH, INFO
- Categoria: LLMOps, DataOps, MLOps, FinOps, Infra
- Período: últimas 6h, 24h, 7d, 30d

### Dados Necessários (BigQuery queries)

#### Query 1: Custo LLM por modelo (últimas 24h)
```sql
SELECT 
  DATE(created_at) as dia,
  model,
  SUM(input_tokens) as input_tokens,
  SUM(output_tokens) as output_tokens,
  ROUND(SUM(cost_usd), 2) as cost_usd
FROM `spepe_mlops.sentinel_state`  -- será spepe_mlops.llm_costs após implementação
WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
GROUP BY dia, model
ORDER BY dia DESC, cost_usd DESC
```

#### Query 2: Status de serviços (health checks)
```sql
SELECT 
  resource_id,
  category,
  status,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), last_check, SECOND) as seconds_ago,
  latency_ms,
  error_msg
FROM `spepe_mlops.sentinel_state`
WHERE category IN ('dataops', 'mlops', 'infra', 'social')
ORDER BY 
  CASE status WHEN 'breach' THEN 1 WHEN 'warn' THEN 2 WHEN 'ok' THEN 3 ELSE 4 END,
  last_check DESC
```

#### Query 3: Alertas timeline (últimas 24h)
```sql
SELECT 
  alert_id,
  timestamp,
  severity,  -- 'breach', 'warn', 'info'
  category,
  message,
  resource_id,
  suggested_action,
  acknowledged
FROM `spepe_mlops.sentinel_alerts`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
ORDER BY timestamp DESC
LIMIT 50
```

### API Endpoints

```
GET /admin/api/sentinel/summary
→ { status, cost_total_month, cost_delta, alerts_count, services_ok }

GET /admin/api/sentinel/costs?period=24h|7d|30d
→ { by_model: {...}, by_service: {...}, forecast: {...} }

GET /admin/api/sentinel/health
→ { services: [{id, status, latency_ms, last_check, error}] }

GET /admin/api/sentinel/alerts?severity=breach|warn|info&category=...
→ { alerts: [{id, timestamp, message, action}], total }

POST /admin/api/sentinel/alert/{alert_id}/acknowledge
→ { success, acknowledged_at }

POST /admin/api/sentinel/action/{action_id}/execute
→ { success, output, execution_time_ms }

GET /admin/api/sentinel/stream
→ WebSocket SSE — emite eventos de custo/status em tempo real
```

### Interações

- **Click em status 🟡/🔴** → Expande card com histórico de 7 dias e recomendação
- **Click em [Detalhes]** → Modal com logs estruturados do serviço
- **Click em [Escalate]** → Cria PagerDuty incident (integração futura)
- **Click em [Executar]** (ação sugerida) → Confirma antes de executar command
- **Filtro de timeline** → Refetch com categoria/status selecionados
- **SSE stream** → Atualiza cards em tempo real a cada 15s

### Componentes UI

- **Health badge**: `<span class="status-badge" data-status="ok|warn|breach">`
- **Sparkline**: Chart.js inline (6 pontos, 24h)
- **Stacked bar**: Chart.js horizontal (modelos/serviços)
- **Timeline**: DIV com repeat, cada item `[timestamp] [icon] [message]`
- **Card**: `<div class="sentinel-card">`

---

## 2. ABA: ARQUITETURA — Diagrama Visual + Status

### Propósito
Visualizar fluxo de dados (Bronze → Silver → Gold → MLOps) e status de cada componente. Detectar gargalos visualmente.

### Layout Base (2 seções)

#### 2.1 DAG Flowchart (central, ~70% do espaço)

**Estrutura (diagrama ASCII para referência):**
```
                        ┌─────────────────────────────────────────┐
                        │        FONTES DE DADOS (21)             │
                        │  TSE | IBGE | Digital | DATASUS | ...  │
                        └────────────────────┬────────────────────┘
                                             │
                                             ▼
                    ┌────────────────────────────────────────────┐
                    │       BRONZE LAYER (GCS Raw)               │
                    │  raw/{source}/{year}/{uf}/  [parquet imut] │
                    │  ✅ 21 fontes | Completude: 95% | Δ 2h    │
                    └────────┬────────────────────────┬───────────┘
                             │                        │
                    ┌────────▼────────┐     ┌─────────▼──────────┐
                    │ normalize_cols()│     │ validate_schema()  │
                    │ map_municipios()│     │ check_nulls()      │
                    │ type_casting()  │     │ dedupe()           │
                    └────────┬────────┘     └─────────┬──────────┘
                             │                        │
                             └────────────┬───────────┘
                                          │
                                          ▼
                    ┌────────────────────────────────────────────┐
                    │       SILVER LAYER (BigQuery)              │
                    │  spepe_silver.tse_* | ibge_* | digital_*  │
                    │  ✅ 17 fontes prontas | Δ 6h | DQ: 92%   │
                    └────────┬────────────────────────┬───────────┘
                             │ tse_resultados        │ ibge_municipio
                             │ tse_candidatos        │ datasus_saude
                             │ digital_trends        │ sancoes
                             │                       │
                    ┌────────▼───────────────────────▼──────────┐
                    │ silver_transform_job (daily 06:00 UTC)    │
                    │ • Join TSE ↔ IBGE código_municipio        │
                    │ • Enrich com contexto socioeconômico      │
                    │ • Compute DQ scores (completude, freshness)│
                    └────────┬──────────────────────────────────┘
                             │
                             ▼
                    ┌────────────────────────────────────────────┐
                    │       GOLD LAYER (BigQuery)                │
                    │  spepe_gold.fact_* | dim_*                │
                    │  ✅ 14 fatos | Particionado | DQ: 99%    │
                    └────────┬──────────────────────┬────────────┘
                             │                      │
              ┌──────────────┼──────────────────────┼────────────┐
              │              │                      │            │
              ▼              ▼                      ▼            ▼
        fact_municipio  fact_candidato        dim_territorio  dim_tempo
        fact_eleicao    fact_ibge              dim_fonte       dim_candidato
        
                             │
                    ┌────────▼──────────────────┐
                    │  FEATURE ENGINEERING      │
                    │  • Agregações             │
                    │  • Feature store prep     │
                    │  • Normalizações          │
                    └────────┬──────────────────┘
                             │
                             ▼
                    ┌────────────────────────────┐
                    │   MLOPS LAYER              │
                    │  • Treinamento (PyMC)     │
                    │  • Predictions             │
                    │  • Evaluations             │
                    │  • Drift & Bias monitoring │
                    └────────┬──────────────────┘
                             │
                    ┌────────▼──────────────────┐
                    │  7 AGENTES (Claude/Gem.)  │
                    │  • Coletor                │
                    │  • Analista               │
                    │  • Perfilador             │
                    │  • Modelista Bayesiano    │
                    │  • Explicador             │
                    │  • Narrador               │
                    │  • Vigilante              │
                    └───────────────────────────┘
```

**Componentes visuais (Mermaid/SVG rendering):**

```
Caixa (componente):
┌─────────────────┐
│ 🟢 TITULO       │  ← cor de status (verde=OK, amarelo=WARN, vermelho=BREACH)
│ ─────────────── │
│ Descrição       │
│ • Métrica 1     │
│ • Métrica 2     │
└─────────────────┘

Seta (fluxo):
   ───▶  = fluxo normal (verde)
   ═══▶  = fluxo com alerta (amarelo)
   ╳╳▶  = fluxo quebrado (vermelho)
```

#### 2.2 Legend & Status Table (right sidebar, ~25%)

**Cores de status:**
- 🟢 OK — Operacional, dentro de SLA
- 🟡 WARN — Degradado, monitorar
- 🔴 BREACH — Falha crítica
- ⚪ UNKNOWN — Não monitorado

**Tabela de status:**

| Componente | Status | Última exec | Duração | Rows | Error |
|------------|--------|-------------|---------|------|-------|
| TSE Ingest | 🟢 OK | 2h atrás | 12m | 2.1M | - |
| IBGE Sync | 🟢 OK | 6h atrás | 5m | 450k | - |
| Silver Transform | 🟡 WARN | 30m ago | 18m | 2.5M | Timeout 1/3 UFs |
| Gold Build | 🔴 BREACH | 8h ago | - | - | Null ref fact_municipio |
| PyMC Train | 🟢 OK | 2d ago | 45m | 2M samples | - |
| Drift Monitor | 🟢 OK | 4h ago | 2m | - | - |
| Bias Monitor | 🟢 OK | 4h ago | 3m | - | - |

**Cards de detalhe (expandível ao clicar na linha):**
```
┌────────────────────────────────────────────┐
│ SILVER TRANSFORM (Job ID: silver-tmdgv)    │
│ ────────────────────────────────────────── │
│ Status: 🟡 WARN (1/3 UFs falhou)           │
│ Última execução: 2h 15m atrás              │
│ Duração: 18m 32s                           │
│ Rows processadas: 2.5M                     │
│                                             │
│ Erro:                                       │
│ > Timeout ao processar UF=RJ                │
│ > Tabela IBGE faltando 12k registros       │
│                                             │
│ Recomendação:                              │
│ ✓ Reexecutar job apenas RJ                │
│ ✓ Verificar latência BigQuery             │
│                                             │
│ [Logs] [Reexecute] [Ignore]                │
└────────────────────────────────────────────┘
```

### Dados Necessários (BigQuery queries)

#### Query 1: Status de cada job (última execução)
```sql
SELECT 
  job_id,
  job_name,
  status,  -- success | running | failed | timeout
  started_at,
  TIMESTAMP_DIFF(ended_at, started_at, SECOND) as duration_sec,
  rows_processed,
  rows_failed,
  error_msg,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), ended_at, MINUTE) as minutes_ago
FROM `spepe_mlops.job_executions`
WHERE 1=1
  AND DATE(started_at) >= CURRENT_DATE() - 7
  AND job_type IN ('bronze', 'silver', 'gold', 'mlops')
QUALIFY ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY started_at DESC) = 1
ORDER BY 
  CASE status WHEN 'failed' THEN 1 WHEN 'timeout' THEN 2 WHEN 'running' THEN 3 ELSE 4 END,
  ended_at DESC
```

#### Query 2: Histórico de execução por job (últimos 30 dias)
```sql
SELECT 
  job_id,
  DATE(started_at) as dia,
  COUNT(*) as executions,
  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failures,
  ROUND(AVG(TIMESTAMP_DIFF(ended_at, started_at, SECOND)), 1) as avg_duration_sec,
  SUM(rows_processed) as total_rows
FROM `spepe_mlops.job_executions`
WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY job_id, dia
ORDER BY dia DESC, job_id
```

### API Endpoints

```
GET /admin/api/architecture/dag
→ { nodes: [{id, name, status, metrics}], edges: [{from, to, status}] }

GET /admin/api/architecture/jobs
→ { jobs: [{id, name, status, last_run, duration, rows, error}] }

GET /admin/api/architecture/job/{job_id}
→ { id, name, status, history: [{date, status, duration, rows, error}] }

GET /admin/api/architecture/lineage?source={source}&target={target}
→ { path: [{job_id, status, duration}] }

POST /admin/api/architecture/job/{job_id}/rerun
→ { success, execution_id, started_at }

GET /admin/api/architecture/health-timeline
→ { timeline: [{timestamp, event, resource, status}] }
```

### Interações

- **Click em caixa** → Expande card com detalhes de execução, logs, opções para rerun
- **Hover na seta** → Mostra contagem de registros e latência
- **Click em [Logs]** → Modal com stdout/stderr estruturado
- **Click em [Reexecute]** → Confirma + inicia job via Cloud Run
- **Timeline control** → Slider para últimas 24h/7d/30d
- **Zoom/Pan** → Se DAG ficar grande, permitir zoom SVG

### Componentes UI

- **DAG Renderer**: Mermaid.js ou vis.js network (SVG)
- **Status badge**: cores conforme Query 1
- **Expandable row**: cada linha da tabela abre detalhes
- **Timeline**: sparkline por job mostrando padrão de sucessos/falhas

---

## 3. ABA: KPIs — Indicadores + Trending

### Propósito
Dashboard de indicadores-chave (DataOps, MLOps, LLMOps, FinOps). Comparações mês-a-mês, alertas de degradação.

### Layout Base (5 seções, tab-based)

#### 3.1 Tab 1: DataOps (qualidade dos dados)

**Cards em grid 2x3:**

| KPI | Métrica | Alvo | Status | Δ mês | Ação |
|-----|---------|------|--------|------|------|
| **Completude** | % registros com todos campos | 99% | 96.8% | ↓2.1% | Investigate TSE |
| **Freshness** | Horas desde última ingestão | <6h | 2h | ✅ | - |
| **Cobertura UFs** | % UFs com dado atual | 100% | 85% (23/27) | ↑10% | Complete SC, AC, RR |
| **Cobertura Anos** | Períodos de dados (2018-2026) | 6/9 | 5/9 | - | Backfill 2019, 2024 |
| **DQ Score** | Média de Silver (0-100) | 95 | 91.2 | ↓1.8 | Monitor IBGE |
| **Dupl. Rows** | % registros duplicados | <1% | 0.3% | ✅ | - |

**Exemplo card detalhado:**
```
┌──────────────────────────────┐
│ 📊 COMPLETUDE                │
│ ──────────────────────────── │
│ 96.8% (↓2.1% vs mês ant.)   │
│                              │
│ Alvo: 99%                    │
│ Status: 🟡 WARN             │
│                              │
│ Histórico (30 dias):         │
│  ████████░ 96.8%            │
│                              │
│ Por fonte:                   │
│ TSE ........... 100.0% ✅   │
│ IBGE .......... 95.2% 🟡   │
│ Digital ....... 87.5% ⚠️   │
│ DATASUS ....... 92.1% 🟡   │
│                              │
│ [Detalhes] [Histórico]      │
└──────────────────────────────┘
```

**Sub-query: Completude por fonte (expandível ao clicar em "Por fonte"):**
```
┌────────────────────────────────────────────────┐
│ COMPLETUDE - BREAKDOWN POR FONTE               │
│ ──────────────────────────────────────────── │
│                                                │
│ TSE                           100.0% ██████   │
│  • pesquisas_eleitorais ........ 100% ✅     │
│  • candidatos_2026 ............. 100% ✅     │
│  • perfil_municipio ............ 100% ✅     │
│                                                │
│ IBGE                           95.2% █████    │
│  • sidra_indicadores ........... 97% 🟡      │
│  • localidades ................. 92% 🟡      │
│  • censo_2010 .................. 95% 🟡      │
│                                                │
│ Digital                        87.5% ████     │
│  • google_trends ............... 85% 🟡      │
│  • meta_ads .................... 90% 🟡      │
│                                                │
│ DATASUS                        92.1% █████    │
│  • indicadores_saude ........... 92% 🟡      │
│                                                │
│ ──────────────────────────────────────────── │
│ Recomendação: Investigar Digital (87.5%)     │
│ [Ignorar] [Escalate]                         │
└────────────────────────────────────────────────┘
```

#### 3.2 Tab 2: MLOps (qualidade do modelo)

**Cards em grid 2x3:**

| KPI | Métrica | Alvo | Status | Δ mês | Ação |
|-----|---------|------|--------|------|------|
| **Brier Score** | Erro médio predição | <0.20 | 0.18 | ↓0.02 | ✅ Track |
| **Drift JS** | Jensen-Shannon div. | <0.10 | 0.08 | ↑0.01 | Monitor |
| **Eval Score LLM** | Avg agentes | >0.85 | 0.91 | ↑0.03 | ✅ Excellent |
| **Bias Ratio** | Max grupo / global | <1.15 | 1.22 | ↑0.07 | 🔴 Investigate |
| **Feature Importance** | Top 5 features | - | Atualizado | - | View |
| **Throughput** | Previsões/min | >100 | 156 | ↑24 | ✅ Scaling |

**Exemplo card Brier Score:**
```
┌──────────────────────────────────────┐
│ 📉 BRIER SCORE                       │
│ ──────────────────────────────────── │
│ 0.18 (↓0.02 vs mês ant.) ✅         │
│                                      │
│ Alvo: < 0.20                        │
│ Status: 🟢 OK                       │
│                                      │
│ Backtest histórico (30 dias):       │
│     0.22 │                          │
│     0.20 │  ┌─┐                     │
│     0.18 │  │ └─┐   ┌─┐            │
│     0.16 │  │   └───┘ └─            │
│           └─────────────────────     │
│                                      │
│ Por cargo:                          │
│ Presidente ........ 0.16 ✅         │
│ Senador ........... 0.19 🟡         │
│ Deputado Fed ...... 0.18 🟡         │
│ Governador ........ 0.20 🟡         │
│                                      │
│ [Detalhes] [Comparar Modelos]       │
└──────────────────────────────────────┘
```

#### 3.3 Tab 3: LLMOps (performance dos agentes)

**Cards em grid 2x3:**

| Agente | Eval Score | Latência P99 | Confiança | Maturidade | Custo/call |
|--------|-----------|--------------|-----------|-----------|-----------|
| Coletor | 0.92 | 2.1s | 95% | ✅ Prod | $0.008 |
| Analista | 0.89 | 3.4s | 90% | ✅ Prod | $0.012 |
| Perfilador | 0.85 | 1.8s | 87% | 🟡 Beta | $0.005 |
| Modelista Bayesiano | 0.91 | 4.2s | 92% | ✅ Prod | $0.015 |
| Explicador | 0.88 | 2.9s | 89% | 🟡 Beta | $0.010 |
| Narrador | 0.93 | 1.5s | 96% | ✅ Prod | $0.006 |
| Vigilante | 0.86 | 2.3s | 84% | 🟡 Beta | $0.007 |

**Exemplo card Agente:**
```
┌────────────────────────────────────┐
│ 🎯 MODELISTA BAYESIANO             │
│ ──────────────────────────────────│
│ Eval: 0.91 ✅  Confiança: 92% 🟢  │
│ Status: ✅ PROD                    │
│                                    │
│ Latência:                         │
│  P50:  1.2s ├─────┤              │
│  P95:  3.1s ├──────────┤          │
│  P99:  4.2s ├─────────────┤       │
│                                    │
│ Tendência (30d):                  │
│ Eval score: ↑0.03 🟢             │
│ Latência P99: ↓0.5s 🟢           │
│ Confiança: ↑2% 🟢               │
│                                    │
│ Modelo: gemini-2.5-pro            │
│ Versão prompt: v2.1               │
│ Budget mensal: $245 / $500        │
│                                    │
│ [Detalhes] [Histórico] [Upgrade]  │
└────────────────────────────────────┘
```

#### 3.4 Tab 4: FinOps (otimização de custo)

**Cards em grid 2x3:**

| Oportunidade | Economia | Implementação | Prioridade |
|--------------|----------|----------------|-----------|
| **Cache BigQuery** | $120/mês | 2h | 🔴 P1 |
| **Modelo lite (Haiku)** | $80/mês | 4h | 🔴 P1 |
| **Batch predict** | $45/mês | 3h | 🟡 P2 |
| **GCS lifecycle** | $12/mês | 1h | 🟡 P2 |
| **Vertex caching** | $30/mês | 6h | 🟡 P2 |
| **CloudSQL → BQ** | $8/mês | 8h | 🟢 P3 |

**Exemplo card oportunidade:**
```
┌────────────────────────────────────┐
│ 💰 CACHE BIGQUERY                  │
│ ──────────────────────────────────│
│ Economia: $120/mês (12% redução)  │
│                                    │
│ Custo atual: $1,000/mês           │
│ Custo projetado: $880/mês         │
│                                    │
│ Implementação:                     │
│ ├─ Ativar BQ query cache           │
│ ├─ TTL: 6 horas                   │
│ ├─ Estimativa: 2h                 │
│ └─ Complexidade: Baixa            │
│                                    │
│ Impacto:                          │
│ • Latência P99: sem mudança        │
│ • Throughput: ↑15% (cache hits)   │
│ • Custo: ↓12%                      │
│                                    │
│ ROI: 6 meses                      │
│                                    │
│ [Aprovado] [Implementar] [Ignorar] │
└────────────────────────────────────┘
```

#### 3.5 Tab 5: Comparação Mês-a-Mês (tabela + chart)

**Tabela histórica:**

| Métrica | Jan | Fev | Mar | Abr | Mai | Δ Abr→Mai |
|---------|-----|-----|-----|-----|-----|----------|
| Completude | 94% | 95% | 96% | 98% | 96.8% | ↓1.2% 🟡 |
| Brier Score | 0.22 | 0.21 | 0.20 | 0.19 | 0.18 | ↓0.01 ✅ |
| Drift JS | 0.15 | 0.12 | 0.11 | 0.09 | 0.08 | ↓0.01 ✅ |
| Custo Total | $850 | $920 | $980 | $1050 | $1020 | ↓$30 ✅ |
| Eval Score | 0.81 | 0.84 | 0.87 | 0.89 | 0.90 | ↑0.01 ✅ |

**Chart (sparklines ou multi-line):**
```
CUSTO TOTAL ($/mês)
  1050 │         ╱─╲
  1000 │    ╱─╱   ╲╱─╲
   950 │  ╱╱         ╲
   900 │╱╱             ╲
   850 │                 ╲
       └─────────────────────
        Jan Feb Mar Apr May
        
BRIER SCORE (predição)
   0.22 │╲
   0.20 │ ╲
   0.18 │  ╲───╱
   0.16 │      │
   0.14 │      └───
       └──────────────
        Jan Feb Mar Apr May
```

### Dados Necessários (BigQuery queries)

#### Query 1: Completude por fonte (últimas 30 dias)
```sql
WITH daily_completeness AS (
  SELECT 
    DATE(extracted_at) as dia,
    source,
    COUNT(*) as total_rows,
    COUNTIF(NOT (col1 IS NULL OR col2 IS NULL OR col3 IS NULL)) as complete_rows,
    ROUND(100 * COUNTIF(NOT (col1 IS NULL OR col2 IS NULL OR col3 IS NULL)) / COUNT(*), 1) as completeness_pct
  FROM `spepe_silver.tse_*` 
  WHERE extracted_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY dia, source
)
SELECT 
  source,
  ROUND(AVG(completeness_pct), 1) as avg_completeness,
  MIN(completeness_pct) as min_completeness,
  MAX(completeness_pct) as max_completeness,
  ARRAY_AGG(STRUCT(dia, completeness_pct) ORDER BY dia) as historical
FROM daily_completeness
GROUP BY source
ORDER BY avg_completeness DESC
```

#### Query 2: Brier score histórico (últimas 30 dias por cargo)
```sql
SELECT 
  DATE(evaluated_at) as dia,
  COALESCE(cargo, 'Total') as cargo,
  COUNT(DISTINCT candidato) as n_predictions,
  ROUND(AVG(brier_score), 4) as avg_brier,
  ROUND(STDDEV(brier_score), 4) as stddev_brier,
  ROUND(MIN(brier_score), 4) as min_brier,
  ROUND(MAX(brier_score), 4) as max_brier
FROM `spepe_mlops.fact_predictions`
WHERE evaluated_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY dia, cargo
ORDER BY dia DESC, cargo
```

#### Query 3: LLMOps per agent (eval + latency)
```sql
WITH eval_data AS (
  SELECT 
    agent,
    eval_id,
    score,
    confidence,
    evaluated_at
  FROM `spepe_mlops.agent_evaluations`
  WHERE evaluated_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
),
latency_data AS (
  SELECT 
    agent,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)] as p50_latency_ms,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] as p95_latency_ms,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)] as p99_latency_ms,
    COUNT(*) as call_count
  FROM `spepe_mlops.agent_calls`
  WHERE called_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  GROUP BY agent
)
SELECT 
  e.agent,
  ROUND(AVG(e.score), 3) as eval_score,
  ROUND(AVG(e.confidence), 3) as avg_confidence,
  l.p50_latency_ms,
  l.p95_latency_ms,
  l.p99_latency_ms,
  l.call_count
FROM eval_data e
LEFT JOIN latency_data l USING (agent)
GROUP BY e.agent, l.p50_latency_ms, l.p95_latency_ms, l.p99_latency_ms, l.call_count
ORDER BY eval_score DESC
```

#### Query 4: Custo estimado (últimas 30 dias)
```sql
SELECT 
  DATE(created_at) as dia,
  'LLM_Tokens' as category,
  ROUND(SUM(cost_usd), 2) as cost_usd
FROM `spepe_mlops.cost_tracking`
WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND category IN ('LLM_Tokens', 'BigQuery', 'CloudRun', 'GCS')
GROUP BY dia, category
ORDER BY dia DESC, category
```

### API Endpoints

```
GET /admin/api/kpis/dataops
→ { completeness, freshness, coverage, dq_score, dedup_rate }

GET /admin/api/kpis/dataops/breakdown?source={source}
→ { by_source: [{source, completeness, historical}] }

GET /admin/api/kpis/mlops
→ { brier_score, drift_js, eval_score, bias_ratio, throughput }

GET /admin/api/kpis/mlops/brier-by-cargo
→ { by_cargo: [{cargo, brier, trend, target}] }

GET /admin/api/kpis/llmops
→ { agents: [{name, eval_score, latency_p99, confidence, maturity}] }

GET /admin/api/kpis/finops/opportunities
→ { opportunities: [{title, savings_monthly, implementation_hours, priority, impact}] }

GET /admin/api/kpis/historical?metric=completeness|brier|cost&period=30d|90d
→ { historical: [{date, value, target}], chart_data }

GET /admin/api/kpis/month-to-month
→ { months: [{month, completeness, brier, drift, cost, eval_score}] }
```

### Interações

- **Click em status card** → Expande com breakdown por fonte/cargo
- **Click em [Detalhes]** → Modal com sparklines históricos + tendência
- **Click em [Comparar Modelos]** → Abre chart comparando modelos (se Multi-modelo)
- **Tab switch** → Refetch dados da aba selecionada
- **Chart hover** → Tooltip mostrando valores diários
- **[Implementar]** na aba FinOps → Cria ticket (Firestore) para implementação

### Componentes UI

- **KPI card**: título + valor grande + status badge + tendência Δ
- **Sparkline**: Chart.js mini chart (20px height)
- **Table**: DataTables.js com sorting/filtering
- **Breakdown modal**: grid com cards menores, um por categoria

---

## 4. ABA: VALIDAÇÃO MANUAL — Data Explorer

### Propósito
Explorar dados Silver/Gold em tempo real. Verificar qualidade, checar anomalias, busca manual. Para analistas/PMs.

### Layout Base (3 seções)

#### 4.1 Filtros Sidebar (left, 20%)

**Estrutura (dropdown + checkboxes):**

```
┌─────────────────────────────────┐
│ 🔍 EXPLORADOR DE DADOS          │
├─────────────────────────────────┤
│                                  │
│ 📊 Dataset                      │
│ ├─ Silver (default)             │
│ ├─ Gold                         │
│ └─ MLOps (readonly)             │
│                                  │
│ 📋 Tabela                       │
│ ├─ tse_resultados ✅            │
│ ├─ tse_candidatos               │
│ ├─ ibge_municipio               │
│ ├─ datasus_saude                │
│ └─ [mostrar mais]               │
│                                  │
│ 🗺️  UF                          │
│ [Todas ▼]                       │
│ ☐ SP  ☐ RJ  ☐ MG ☐ RS          │
│ ☐ BA  ☐ PR  ☐ PE ☐ SC          │
│ [✓ Todas] [✓ Selecionadas]     │
│                                  │
│ 📅 Data Range                   │
│ De: [2022-01-01] ◀ ▶            │
│ Até: [2026-05-12] ◀ ▶           │
│ [Últimos 30 dias] [Tudo]        │
│                                  │
│ 🎯 Cargo                        │
│ [Todos ▼]                       │
│ ☐ Presidente                    │
│ ☐ Senador                       │
│ ☐ Deputado Federal              │
│ ☐ Governador                    │
│                                  │
│ 📌 Coluna adicional             │
│ [Nenhuma ▼]                     │
│                                  │
│ ┌─────────────────────────────┐ │
│ │ [🔍 Search] [🔄 Reset]     │ │
│ │ [👁️ Preview] [📥 Export]    │ │
│ └─────────────────────────────┘ │
└─────────────────────────────────┘
```

#### 4.2 Preview Grid (center, 65%)

**Tabela com dados, scrollável:**

```
┌──────────────────────────────────────────────────────────────────┐
│ tse_resultados (Silver) | 2,156,342 rows | 6 colunas visíveis   │
├──────────────────────────────────────────────────────────────────┤
│ # │ candidato      │ sg_uf │ cargo    │ votos │ % │ data_eleicao │
├──────────────────────────────────────────────────────────────────┤
│ 1 │ Jair Bolsonaro │ SP    │ Pres     │ 2.1M  │ 40.9% │ 2022-10-30│
│ 2 │ Lula           │ SP    │ Pres     │ 2.0M  │ 40.1% │ 2022-10-30│
│ 3 │ Ciro Gomes     │ RJ    │ Pres     │ 490k  │ 9.5%  │ 2022-10-30│
│ 4 │ Simone Tebet   │ MG    │ Pres     │ 215k  │ 4.2%  │ 2022-10-30│
│ 5 │ Eymael         │ SC    │ Pres     │ 65k   │ 1.3%  │ 2022-10-30│
│ ...
│ 1000 │ [carregando mais 50 linhas]                              │
├──────────────────────────────────────────────────────────────────┤
│ Mostrando 50-100 de 2,156,342 | [◀ Prev] [1] [2] [3]... [Next ▶]│
└──────────────────────────────────────────────────────────────────┘
```

**Recursos:**
- Coluna clicável para sort (▲▼ indicator)
- Coluna com valores NULL em destaque (cinza)
- Celula com overflow mostra tooltip ao hover
- Linha alternada (cinza/branco)
- Row height compacta (28px)

#### 4.3 Detalhes de Linha (right sidebar, 20%, colapsível)

**Expandido ao clicar em uma linha:**

```
┌─────────────────────────────────┐
│ DETALHES - Linha #237           │
├─────────────────────────────────┤
│                                  │
│ candidato: Jair Bolsonaro       │
│ sg_uf: SP                        │
│ cargo: Presidente               │
│ votos: 2,100,000                │
│ percentual: 40.9%               │
│ data_eleicao: 2022-10-30        │
│                                  │
│ [Mais colunas]                   │
│ extracted_at: 2024-05-12 10:23  │
│ dq_score: 98                    │
│ _updated_at: 2024-05-12 10:25   │
│                                  │
│ 📊 Relacionados                 │
│ • Senador: 3 registros          │
│ • Deputado Federal: 8 registros │
│ • Governador: 1 registro        │
│                                  │
│ [Ver tudo] [Exportar JSON]      │
└─────────────────────────────────┘
```

#### 4.4 Busca Full-Text (top)

**Search box com auto-complete:**

```
🔍 [buscar em "tse_resultados"...     ] [⚙️ Avançado]
   Resultado: 234 matches em "candidato"
   • Jair Bolsonaro (2.1M votos)
   • Jefferson Bolsonaro (145k votos)
   • Ciro Gomes (490k votos)
   ... [mostrar mais]
```

**Busca avançada (modal):**
```
┌────────────────────────────────────────────┐
│ BUSCA AVANÇADA                             │
│ ────────────────────────────────────────── │
│                                            │
│ Campo: [candidato ▼]                      │
│ Operador: [contém ▼]  (contém|=|>|<|∈)   │
│ Valor: [Bolsonaro___]                     │
│                                            │
│ [+ Adicionar condição]                    │
│                                            │
│ Lógica: [E ▼] (E | OU)                   │
│ Campo: [sg_uf ▼]                         │
│ Operador: [= ▼]                          │
│ Valor: [SP ▼]                            │
│                                            │
│ ┌──────────────────────────────────────┐ │
│ │ [✕ Limpar] [🔍 Buscar] [💾 Salvar]  │ │
│ └──────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

### Dados Necessários (BigQuery queries)

#### Query 1: Lista de tabelas disponíveis + metadados
```sql
SELECT 
  table_schema,
  table_name,
  ROUND(size_bytes / 1e9, 2) as size_gb,
  row_count,
  DATE(TIMESTAMP_MILLIS(creation_time)) as created_date
FROM `spepe-dev.spepe_silver.__TABLES__`
UNION ALL
SELECT 
  table_schema,
  table_name,
  ROUND(size_bytes / 1e9, 2) as size_gb,
  row_count,
  DATE(TIMESTAMP_MILLIS(creation_time)) as created_date
FROM `spepe-dev.spepe_gold.__TABLES__`
ORDER BY size_gb DESC
```

#### Query 2: Coluna names + types para UI dropdown
```sql
SELECT 
  column_name,
  data_type,
  is_nullable
FROM `spepe-dev.spepe_silver.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = @table_name
ORDER BY ordinal_position
```

#### Query 3: Data preview (genérico, substitui @table_name no SQL)
```sql
SELECT * FROM `spepe-dev.{dataset}.{table_name}`
LIMIT @limit OFFSET @offset
```

#### Query 4: Full-text search (se usando termos simples)
```sql
SELECT 
  * 
FROM `spepe-dev.spepe_silver.{table_name}`
WHERE 
  CAST({column_name} AS STRING) ILIKE @search_term
LIMIT @limit
```

### API Endpoints

```
GET /admin/api/explorer/tables
→ { tables: [{schema, name, size_gb, row_count, created_date}] }

GET /admin/api/explorer/columns?dataset={silver|gold}&table={table_name}
→ { columns: [{name, type, nullable, sample_values: []}] }

GET /admin/api/explorer/data?dataset={dataset}&table={table_name}&limit=50&offset=0&filters={...}
→ { 
    data: [[row_values]],
    total_rows,
    columns: [{name, type}],
    execution_time_ms
  }

GET /admin/api/explorer/search?dataset={dataset}&table={table_name}&q={query}&column={column}
→ { matches: [{row_id, matching_text, context}], total_matches }

POST /admin/api/explorer/export?dataset={dataset}&table={table_name}&filters={...}&format={csv|json|parquet}
→ { file_url, size_mb, execution_time_ms }

GET /admin/api/explorer/sample-values?dataset={dataset}&table={table_name}&column={column}
→ { values: [...], distinct_count, null_count, data_type }
```

### Interações

- **Click em tabela** → Carrega colunas dinâmicas e preview (primeiras 50 linhas)
- **Click em coluna de sort** → Ordena asc/desc (▲▼)
- **Click em linha** → Expande sidebar com detalhes completos
- **Busca** → Executa ILIKE busca full-text em real-time (debounce 300ms)
- **[Avançado]** → Abre modal de busca com múltiplas condições
- **[Preview]** → Chama Query 3 com limite padrão (50)
- **[Exportar]** → Dialog para escolher formato (CSV/JSON/Parquet) + download
- **Filtro UF** → Altera cláusula WHERE dinamicamente
- **Filtro Date Range** → Adiciona condição temporal à query

### Componentes UI

- **Sidebar**: flex column, input selects + checkboxes, sticky no topo
- **Data grid**: virtual scrolling (se >1k rows), cells editáveis-readonly, alternancia cores
- **Search input**: debounce, auto-complete dropdown (Typeahead.js)
- **Modal busca avançada**: form dinâmica com inputs + buttons
- **Detail panel**: JSON-like estrutura, copy-to-clipboard por campo

---

## 5. ABA: VALIDAÇÃO DO MODELO — SHAP + Features + Matriz de Confusão

### Propósito
Análise profunda de predictor: features importâncias (SHAP), amostras por período, estratégia de confiança, ROC/CM. Para data scientists.

### Layout Base (4 seções)

#### 5.1 Overview Cards (topo, grid 1x4)

**Cards resumo do modelo:**

| Card | Métrica | Detalhe |
|------|---------|---------|
| **Versão** | v2.1-202405 | Última retrain: 2d atrás |
| **Holdout Performance** | Brier: 0.18 | Backtest 2022: 0.19, 2024: 0.17 |
| **Feature Store** | 47 features | 6 inputs + 41 engineered |
| **Training Data** | 2.1M samples | Período: 2018-2026, 27 UFs |

**Exemplo card:**
```
┌────────────────────────────────┐
│ 📊 HOLDOUT PERFORMANCE         │
│ ────────────────────────────── │
│ Brier Score: 0.18              │
│ Accuracy (threshold 0.5): 82%  │
│ ROC-AUC: 0.89                  │
│ Log Loss: 0.41                 │
│                                │
│ Benchmark:                     │
│ vs Random: +8x melhor          │
│ vs Prev Model: ↑0.02 (↑10%)   │
│                                │
│ [Detalhes]                     │
└────────────────────────────────┘
```

#### 5.2 Feature Importance — SHAP Force Plot

**Seção esquerda (40%):**

```
┌──────────────────────────────────────────┐
│ 🎯 FEATURE IMPORTANCE (SHAP Mean Abs)    │
├──────────────────────────────────────────┤
│                                          │
│ 1. % Zona Rural                          │
│    ████████████████ 0.156 (35%)          │
│                                          │
│ 2. Renda Média Domiciliar                │
│    ██████████████ 0.134 (30%)            │
│                                          │
│ 3. IDH Municipal                         │
│    █████████ 0.089 (20%)                 │
│                                          │
│ 4. Votos TSE 2022                        │
│    ████████ 0.052 (12%)                  │
│                                          │
│ 5. Sentimento Social (Digital)           │
│    █ 0.009 (2%)                          │
│                                          │
│ [Mostrar + 42 features ocultas]          │
│                                          │
│ [SHAP Dependence] [SHAP Waterfall]       │
└──────────────────────────────────────────┘
```

**SHAP Force Plot (ao clicar em feature, central-bottom):**
```
┌──────────────────────────────────────────────────────────────┐
│ SHAP FORCE PLOT — % Zona Rural = 42%                         │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Base value: 0.45  │  Predictor: 0.62  │  Δ: +0.17         │
│                                                              │
│  ◀─────────────────────────────────────────────────────────▶│
│  0.30                                                  0.70  │
│  baixa                                                 alta  │
│                                                              │
│  % Zona Rural = 42%   SHAP = +0.12  ║████  Aumenta pred   │
│  Renda = $800/mês     SHAP = +0.05  ║██    Aumenta pred   │
│  IDH = 0.65           SHAP = -0.02  ░░     Diminui pred   │
│                                                              │
│  Interpretação:                                             │
│  Zona rural 42% → forte indicador de voto em candidato    │
│                                                              │
│ [Dependência] [Waterfall] [Exemplos]                        │
└──────────────────────────────────────────────────────────────┘
```

#### 5.3 Distribuição de Features (direita, 40%)

**Histogramas das top 5 features:**

```
┌────────────────────────────────────────────┐
│ DISTRIBUIÇÃO — TOP 5 FEATURES              │
├────────────────────────────────────────────┤
│                                            │
│ % Zona Rural                               │
│ Histórico: [███████████████░░]  (training) │
│ Holdout:   [████████████░░░░]  (test)    │
│ Hoje:      [██████████░░░░░░░]  (live)    │
│                                            │
│ Renda Média Domiciliar                     │
│ Training: [██████████████░░]               │
│ Test:     [████████████░░░░]              │
│ Live:     [█████████░░░░░░░░░]            │
│                                            │
│ IDH Municipal                              │
│ Training: [███████████░░░░░]              │
│ Test:     [███████████░░░░░]              │
│ Live:     [████████░░░░░░░░░░]            │
│                                            │
│ Votos TSE 2022                             │
│ Training: [█████████░░░░░░░░░]            │
│ Test:     [████████░░░░░░░░░░]            │
│ Live:     [██████░░░░░░░░░░░░░]           │
│                                            │
│ Sentimento Social                          │
│ Training: [████░░░░░░░░░░░░░░░░]          │
│ Test:     [████░░░░░░░░░░░░░░░░]          │
│ Live:     [█████░░░░░░░░░░░░░░░░]         │
│                                            │
│ 🟡 ALERTA: Distribuição "Live" diferente  │
│    da "Training" → indica possível drift   │
└────────────────────────────────────────────┘
```

#### 5.4 Matriz de Confusão + ROC Curve (bottom, grid 2x2)

**Quadrante 1: Matriz de Confusão**
```
┌─────────────────────────────────┐
│ CONFUSION MATRIX (threshold=0.5)│
├─────────────────────────────────┤
│               Predito:          │
│            Pos    |    Neg      │
│ Real    Pos  4850  |   320  │   │
│ Pos/Neg   80% |  6%  │ TP  | FN│
│                     │────────    │
│         Neg  620   |  9210  │   │
│          13% | 68%  │ FP  | TN│
│                                 │
│ Acurácia: 82%                  │
│ Sensibilidade: 93%             │
│ Especificidade: 74%            │
│ Precisão: 74%                  │
│ F1-score: 0.83                 │
└─────────────────────────────────┘
```

**Quadrante 2: ROC Curve**
```
┌─────────────────────────────────┐
│ ROC CURVE (AUC = 0.89)          │
├─────────────────────────────────┤
│                                 │
│   1.0 ┌────────────────────────┐│
│   0.8 │                   ╱────│
│   0.6 │             ╱────╱     │
│   0.4 │       ╱────╱           │
│   0.2 │ ╱────╱                 │
│   0.0 └──────────────────────┬─┘
│       0.0   0.5   1.0        │
│       False Positive Rate    │
│                              │
│ Linha diagonal = Random      │
│ Curva acima = Bom modelo    │
│ AUC > 0.85 = Excelente      │
└─────────────────────────────────┘
```

**Quadrante 3: Threshold Exploration**
```
┌─────────────────────────────────┐
│ THRESHOLD VS METRICS            │
├─────────────────────────────────┤
│                                 │
│ Acurácia:  ███████████░░░░░░░  │
│ Sens:      ████████████████░░  │
│ Espec:     ████░░░░░░░░░░░░░░  │
│            │──────────────────  │
│            0.3   0.5   0.7    │
│                                 │
│ Threshold recomendado: 0.50    │
│ (Balanço sens/espec)           │
│                                 │
│ Histórico por período:          │
│ 2022: 0.48  (82% acc)          │
│ 2024: 0.52  (84% acc)          │
│ 2026: 0.50  (82% acc)          │
└─────────────────────────────────┘
```

**Quadrante 4: Calibration Curve**
```
┌─────────────────────────────────┐
│ CALIBRATION CURVE               │
├─────────────────────────────────┤
│ Prob prevista vs Freq observada │
│                                 │
│   1.0 ├────────────────────────┤
│   0.8 │        ╱  ← calibrated│
│   0.6 │    ╱ ╱  modelo OK     │
│   0.4 │  ╱╱                   │
│   0.2 │╱                      │
│   0.0 ├──────────────────────┤
│       0.0   0.5   1.0        │
│       Prob Prevista          │
│                              │
│ Diagonal perfeita = bem cal. │
│ Acima = super-confiante      │
│ Abaixo = sub-confiante       │
│                              │
│ Expected Calibration Error:  │
│ ECE = 0.045 (bom)           │
└─────────────────────────────────┘
```

#### 5.5 Amostras por Período/Cargo (tab 1: Temporal)

**Tabela amostras de treino por período:**

| Período | Cargo | N Amostras | % | Brier Holdout | Δ vs média |
|---------|-------|-----------|---|--------------|-----------|
| 2018 | Presidente | 450k | 21% | 0.19 | +0.01 |
| 2018 | Senador | 250k | 12% | 0.20 | +0.02 |
| 2022 | Presidente | 520k | 25% | 0.17 | -0.01 |
| 2022 | Senador | 280k | 13% | 0.19 | +0.01 |
| 2026 | Presidente | 310k | 15% | 0.18 | 0.00 |
| 2026 | Senador | 190k | 9% | 0.18 | 0.00 |
| Holdout | - | 2.1M | 100% | 0.18 | - |

#### 5.6 Estratégia de Confiança (tab 2: Confidence Estratégia)

**Card narrativo (como o modelo chega a nível de confiança):**

```
┌────────────────────────────────────────────────────────────┐
│ 🎯 ESTRATÉGIA DE CONFIANÇA PARA PREDICTOR                  │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ O modelo SPEPE Bayesiano atinge 92% confiança via:        │
│                                                            │
│ 1. DADOS ESTRUTURAIS (35% da confiança)                   │
│    └─ IBGE municipio + DATASUS saúde + TSE histórico      │
│    └─ 2.1M amostras 2018-2026, 27 UFs                    │
│    └─ Score: 35/35 (público, imutável, auditado)         │
│                                                            │
│ 2. MODELO BAYESIANO + IC 95% (40% da confiança)           │
│    └─ PyMC 3.11 com NUTS sampler (4k iterações)          │
│    └─ Convergence: R̂ < 1.01 para todos parâmetros       │
│    └─ Backtest Brier: 0.18 (vs random 0.25)             │
│    └─ Score: 40/40 (transparente, testado, auditado)     │
│                                                            │
│ 3. VALIDAÇÃO CRUZADA (15% da confiança)                   │
│    └─ Leave-One-UF-Out (LOUO): 27 folds                  │
│    └─ Brier médio: 0.20 (vs 0.18 holdout) → estável     │
│    └─ Score: 12/15 (bom, -3 por LOUO > holdout)         │
│                                                            │
│ 4. DRIFT & BIAS MONITORING (10% da confiança)             │
│    └─ JS divergence: 0.08 (< 0.10 threshold) ✅          │
│    └─ Bias ratio max: 1.22 (> 1.15 threshold) ⚠️         │
│    └─ Score: 8/10 (-2 por bias detectado)                │
│                                                            │
│ ────────────────────────────────────────────────────────── │
│ CONFIANÇA TOTAL: (35 + 40 + 12 + 8) / 100 = 92% ✅       │
│                                                            │
│ Próximas ações:                                           │
│ • Reduzir bias em Q2 → Alvo 95%                          │
│ • Retraining trimestral vs mensal                        │
│ • Adicionar sentimento social (Fase 2)                   │
└────────────────────────────────────────────────────────────┘
```

### Dados Necessários (BigQuery queries)

#### Query 1: Feature importance (SHAP)
```sql
SELECT 
  feature_name,
  shap_mean_abs,
  shap_mean,
  shap_std,
  rank_by_importance,
  data_type,
  source_dataset
FROM `spepe_mlops.feature_importance`
WHERE model_version = @model_version
ORDER BY rank_by_importance ASC
LIMIT 50
```

#### Query 2: Confusion matrix (holdout set)
```sql
SELECT 
  SUM(CASE WHEN y_true = 1 AND y_pred_binary = 1 THEN 1 ELSE 0 END) as tp,
  SUM(CASE WHEN y_true = 0 AND y_pred_binary = 1 THEN 1 ELSE 0 END) as fp,
  SUM(CASE WHEN y_true = 1 AND y_pred_binary = 0 THEN 1 ELSE 0 END) as fn,
  SUM(CASE WHEN y_true = 0 AND y_pred_binary = 0 THEN 1 ELSE 0 END) as tn,
  ROUND(100 * SUM(CASE WHEN y_true = y_pred_binary THEN 1 ELSE 0 END) / COUNT(*), 1) as accuracy_pct
FROM `spepe_mlops.fact_predictions`
WHERE model_version = @model_version
  AND set_type = 'holdout'
```

#### Query 3: Amostras por período (training)
```sql
SELECT 
  ano_eleicao,
  cargo,
  COUNT(*) as n_samples,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct,
  ROUND(AVG(brier_score), 4) as avg_brier_holdout
FROM `spepe_gold.fact_eleicao`
WHERE model_version = @model_version
  AND set_type = 'training'
GROUP BY ano_eleicao, cargo
ORDER BY ano_eleicao ASC, cargo
```

#### Query 4: Distribuição features (training vs test vs live)
```sql
SELECT 
  feature_name,
  'training' as dataset,
  APPROX_QUANTILES(feature_value, 100)[OFFSET(25)] as q25,
  APPROX_QUANTILES(feature_value, 100)[OFFSET(50)] as q50,
  APPROX_QUANTILES(feature_value, 100)[OFFSET(75)] as q75,
  STDDEV(feature_value) as stddev
FROM `spepe_mlops.feature_distributions`
WHERE model_version = @model_version
GROUP BY feature_name, dataset
ORDER BY feature_name
```

### API Endpoints

```
GET /admin/api/model-validation/overview
→ { version, brier_score, feature_count, training_samples, holdout_performance }

GET /admin/api/model-validation/feature-importance?topn=10
→ { features: [{name, shap_mean_abs, rank, shap_mean, shap_std}] }

GET /admin/api/model-validation/shap-force?feature={feature_name}&sample_id={id}
→ { base_value, prediction, feature_contributions: [{feature, shap, direction}] }

GET /admin/api/model-validation/confusion-matrix
→ { tp, fp, fn, tn, accuracy, sensitivity, specificity, precision, f1 }

GET /admin/api/model-validation/roc-curve
→ { curve: [{fpr, tpr}], auc, threshold_optimal }

GET /admin/api/model-validation/calibration-curve
→ { curve: [{prob_predicted, freq_observed}], ece }

GET /admin/api/model-validation/samples-by-period
→ { periods: [{year, cargo, n_samples, pct, brier_holdout}] }

GET /admin/api/model-validation/feature-distributions?feature={feature_name}
→ { training: {q25, q50, q75, stddev}, test: {...}, live: {...}, drift_detected }

GET /admin/api/model-validation/confidence-narrative
→ { score: 92, components: [{name, score, description}], actions: [] }
```

### Interações

- **Click em feature rank** → Expande SHAP Force Plot
- **Hover em distribuição** → Tooltip mostrando valores numéricos
- **Tab Temporal** → Tabela amostras, click em linha → detalha cargo/período
- **Tab Confidence** → Card narrativo expandível (clique em cada seção)
- **[Detalhes]** nos cards overview → Modal com métricas adicionais
- **Download plot** → Botão para exportar plots como PNG

### Componentes UI

- **Feature bar chart**: Chart.js horizontal bar (SHAP values)
- **SHAP Force plot**: Librosa.js ou custom SVG (água-forte visual)
- **Distribuição histogramas**: Chart.js stacked histogram
- **Matriz confusão**: Table 2x2 com heatmap colors
- **ROC + Calibration**: Chart.js line plots
- **Confidence narrative**: Cards com progress bars inline

---

## Resumo — Wireframes + Componentização

### Estrutura comum (todas as 5 abas)

```
┌─────────────────────────────────────────────┐
│ TOPNAV: Logo | Breadcrumb | User Pill       │  (height: 50px)
├─────────────────────────────────────────────┤
│ MAIN-TABS: Admin | Sentinel | Arquitetura  │  (height: 40px)
│            | KPIs | Validação Manual        │
│            | Validação do Modelo            │
├──────────────────────────────────────────────┤
│                                              │
│ SIDEBAR (abas específicas) │ MAIN CONTENT  │  (flex: 1)
│                            │                │
│ • Filtros                  │ • Cards        │
│ • Legendas                 │ • Grids        │
│ • Controles                │ • Tables       │
│ • Search                   │ • Charts       │
│                            │                │
│                            │ • Modals       │
│                            │ • Expandables  │
│                            │                │
│                            │ • Scroll       │
│                            │ • Pagination   │
└──────────────────────────────────────────────┘
```

### Componentes Reutilizáveis

| Componente | Abas | Descrição |
|-----------|------|-----------|
| `KPICard` | Sentinel, KPIs | Valor grande + status + trending |
| `StatusBadge` | Todas | Cor + texto (🟢🟡🔴) |
| `DataGrid` | Validação Manual | Virtual scroll, sort, filter |
| `SparklineChart` | KPIs, Sentinel | Mini chart (6-20 pontos) |
| `ModalExpand` | Todas | Overlay modal com detalhes |
| `FilterSidebar` | Validação Manual, Arquitetura | Dropdowns + checkboxes |
| `TabNavigation` | KPIs, Validação Modelo | Tab switcher |
| `TimelineCard` | Sentinel, Arquitetura | Event list com timestamp |
| `ExpandableRow` | Data tables | Row → detalhes laterais |
| `SearchInput` | Validação Manual | Text + auto-complete |
| `ExportButton` | Validação Manual | CSV/JSON/Parquet |

---

## Resumo Executivo — Estimativas

### Por Aba

| Aba | Complexidade | Queries BQ | API Endpoints | Frontend | Total h |
|-----|--------------|-----------|---------------|----------|---------|
| 1. Sentinel | Média-Alta | 3 | 7 | 8h | 18h |
| 2. Arquitetura | Média | 2 | 5 | 6h | 13h |
| 3. KPIs | Média | 4 | 7 | 7h | 18h |
| 4. Validação Manual | Alta | 4 | 5 | 10h | 19h |
| 5. Validação Modelo | Alta | 4 | 8 | 9h | 21h |
| **TOTAL** | **Média-Alta** | **17** | **32** | **40h** | **89h** |

### Por Fase

| Fase | Abas | Tempo | Bloqueadores |
|------|------|-------|--------------|
| 1: Backend (BQ + APIs) | 5 | 30h | - |
| 2: Frontend (UI + interações) | 5 | 40h | BigQuery queries prontas |
| 3: Testes + integração | 5 | 19h | Backend completo |

### Sequência Recomendada

1. **Week 1 (30h):** BigQuery schemas + queries (5 abas)
2. **Week 2 (35h):** FastAPI endpoints (5 abas)
3. **Week 3 (24h):** Frontend componentes + CSS (5 abas)
4. **Week 4 (10h):** Testes + refinamento

---

## Dados Resumidos — BigQuery Necessário

### Novas Tabelas Recomendadas

| Tabela | Dataset | Propósito |
|--------|---------|-----------|
| `sentinel_state` | spepe_mlops | Status atual de serviços |
| `sentinel_alerts` | spepe_mlops | Histórico de alertas |
| `job_executions` | spepe_mlops | Log de execução de jobs |
| `llm_costs` | spepe_mlops | Breakdown de custos por modelo/agente |
| `agent_evaluations` | spepe_mlops | Scores eval LLM históricos |
| `agent_calls` | spepe_mlops | Logs de chamadas (latency, tokens) |
| `feature_importance` | spepe_mlops | SHAP importance ranking |
| `feature_distributions` | spepe_mlops | Distribuições features (train/test/live) |
| `cost_tracking` | spepe_mlops | Custo diário por categoria |

**Obs.:** Algumas tabelas já existem (fact_predictions, model_evaluations, sentinel_state).

---

## Próximos Passos

1. **Aprovação de design** — Confirmar com PM/stakeholders
2. **Criação de tarefas** — Sprint planning para as 5 abas
3. **Implementação Backend** — BigQuery + FastAPI (prioridade: Sentinel > Arquitetura > KPIs)
4. **Implementação Frontend** — CSS + interações (reuso máximo de componentes)
5. **Testes + Deploy** — Validação E2E, canary 10%, monitoar alerts

---

## Referências

- **CLAUDE.md:** Arquitetura SPEPE, 7 agentes, Medallion layer
- **Memory audit:** 21 fontes, 17 prontas, status real
- **BigQuery state:** Datasets spepe_silver, spepe_gold, spepe_mlops já existem
- **UI atual:** Dashboard HTML (Chart.js), FastAPI (routers), Chainlit (chat)

---

**Design Doc Status: ✅ COMPLETO**

Wireframes detalhados + API specs + queries BigQuery + componentes UI prontos para implementação.

**Próximo:** Aguardar aprovação + iniciar Task #1 Backend (Sentinel queries).
