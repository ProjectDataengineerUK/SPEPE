---
feature: REDES_SOCIAIS_V12
phase: brainstorm
version: 1.0
date: 2026-05-07
status: ready-for-define
next_phase: /define .claude/sdd/features/BRAINSTORM_REDES_SOCIAIS_V12.md
---

# Brainstorm — Redes Sociais v1.2

## Metadados

| Campo | Valor |
|-------|-------|
| Feature | REDES_SOCIAIS_V12 |
| Data | 2026-05-07 |
| Escopo | **Produto** (não MVP) |
| Status | ready-for-define |
| Próxima fase | `/define .claude/sdd/features/BRAINSTORM_REDES_SOCIAIS_V12.md` |

---

## Ideia Inicial

SPEPE precisa de um **módulo de inteligência de redes sociais** para monitorar a percepção eleitoral em quasi-tempo-real para 2026. O módulo já tem ~70% de infraestrutura implementada (`social_client.py` com Twitter/X, Facebook, YouTube; `social_ingest_job.py`; `transform_social_to_silver()`; `fact_social_municipio` no Gold). A v1.2 foca em **ativar, expandir e elevar a qualidade** para nível de produto.

**Problema central:** O módulo atual roda com Bluesky + RSS + sentimento rule-based. Para 2026, o produto precisa de cobertura multi-plataforma, sentimento via Vertex AI NLP, detecção de crise e candidatos dinâmicos.

---

## Perguntas de Descoberta

### Q1 — Objetivo principal
**Pergunta:** Qual é o objetivo principal do módulo Social para 2026?

| Opção | Descrição |
|-------|-----------|
| (a) | Radar de narrativa — temas dominantes por UF/semana |
| (b) | Sentimento por candidato — percepção nas redes |
| (c) | Detecção de crise — ataque coordenado ou viral negativo |
| **(d) ✅** | **Os três juntos — narrativa + sentimento + crise como camadas** |

**Implicação:** Pipeline em 3 camadas progressivas: coleta → enriquecimento → detecção de anomalia.

---

### Q2 — Hierarquia de plataformas
**Contexto levantado no diálogo:**
- YouTube já implementado e ingerido em prod
- Facebook tem META_APP_TOKEN no Secret Manager, mas bloqueado por falta de lista de páginas dos candidatos
- Twitter/X é secundário; Reddit e Google Trends já estão ativos em outros jobs
- source_registry.py já tem classificação completa por score/tipo/viés

**Decisão:** Fazer mapeamento completo de todas as alternativas. Hierarquia de ativação definida pelo inventário de fontes.

---

### Q3 — Frequência de atualização
**Pergunta:** O módulo social precisa de análise em tempo real ou diária consolidada?

| Opção | Descrição |
|-------|-----------|
| (a) | Diária consolidada — 1×/dia, simples, baixo custo |
| (b) | Quase-tempo-real — a cada 3-4h |
| (c) | Streaming — Pub/Sub + Dataflow, latência de minutos |
| **(d) ✅** | **Híbrido — RSS/Trends diário; Twitter/YouTube/Facebook 4×/dia; streaming só para crise** |

**Implicação:** Cloud Scheduler com múltiplas frequências + Pub/Sub para threshold de crise (infra já existe no Terraform).

---

## Inventário de Fontes

### Classificação completa — source_registry.py existente

| Fonte | Score | Tipo | Credencial | Status v1.2 |
|-------|-------|------|-----------|-------------|
| agencia_brasil | 9.0 | oficial | Não precisa | ✅ Ativo via RSS |
| poder360 | 8.5 | imprensa | Não precisa | ✅ Ativo via RSS |
| g1 / folha / o_globo / estadao | 8.0 | imprensa | Não precisa | ✅ Ativo via RSS |
| gdelt | 8.0 | agregador | Não precisa | ⚠️ Desabilitado (rate limit) → reabilitar |
| cnn_brasil | 7.5 | imprensa | Não precisa | ✅ Ativo via RSS |
| google_trends | 7.0 | agregador | Não precisa (pytrends) | ✅ Mover de digital_ingest → social_ingest |
| r7 / metropoles | 6.5 | portal | Não precisa | ✅ Ativo via RSS |
| youtube | 5.0 | social | YOUTUBE_API_KEY | ✅ Ingerido em prod |
| bluesky | 4.5 | social | Não precisa | ✅ Ativo |
| reddit | 4.5 | social | Não precisa | ✅ Job separado |
| twitter_x | 4.0 | social | X_BEARER_TOKEN (Basic $100/mês) | ⚠️ **Secundário — custo alto** |
| facebook | 4.0 | social | META_APP_TOKEN ✅ SM | ⚠️ Falta lista de páginas candidatos |
| instagram | 4.0 | social | Mesma chave do Facebook | 🆕 Implementar v1.2 |
| tiktok | — | social | Research API acadêmica | 🆕 Solicitar + implementar v1.2 |
| telegram | — | social | Sem API oficial | ❌ Fora do escopo |

### Hierarquia de ativação

```
YouTube (✅) > Facebook (páginas) > Instagram > Google Trends > Reddit (✅) > Bluesky (✅) > Twitter/X (budget-gated) > TikTok (Research API)
```

---

## Inventário de Código Existente

### Já implementado (reuso total)

| Componente | Localização | Status |
|-----------|------------|--------|
| Twitter/X, Facebook, YouTube clients | dataops/clients/social_client.py | ✅ |
| Bluesky client | dataops/clients/bluesky_client.py | ✅ |
| News RSS client (8 feeds) | dataops/clients/news_rss_client.py | ✅ |
| GDELT client | dataops/clients/gdelt_client.py | ⚠️ Desabilitado |
| Google Trends | dataops/clients/digital_client.py | ✅ pytrends |
| Reddit client | dataops/clients/reddit_client.py | ✅ |
| social_ingest_job | dataops/jobs/social_ingest_job.py | ✅ condicional em secrets |
| Silver transform | dataops/silver_transformer.py:491 | ✅ |
| Gold fact_social_municipio | dataops/gold_builder.py | ✅ |
| Source registry (score/tipo/viés) | dataops/source_registry.py | ✅ |
| Vertex AI NLP sentiment | social_ingest_job.py (enrich_sentiment_vertex) | ✅ ativa com GCP_PROJECT_ID |
| Cloud Run Job spepe-social-ingest | infra/terraform/cloud_run_jobs.tf | ✅ |
| Pub/Sub topic drift-detected | infra/terraform/pubsub.tf | ✅ |

### A construir

| Componente | Prioridade | Esforço |
|-----------|-----------|---------|
| Instagram client (`fetch_instagram_posts`) | Alta | Baixo — mesma API Facebook Graph |
| `scripts/discover_candidate_pages.py` | Alta | Médio |
| `candidatos_discovery_job.py` (mensal) | Alta | Médio |
| `dim_candidato_social_pages` (BQ table) | Alta | Baixo |
| GDELT fix: backoff exponencial + cache GCS 30min | Média | Baixo |
| Cloud Scheduler múltiplas frequências | Média | Baixo |
| Vigilante agent: threshold crise → Pub/Sub | Média | Médio |
| `tiktok_client.py` | Média | Alto (depende de aprovação API) |

---

## Abordagens Exploradas

### Approach A: Activate + Enrich + Expand ⭐ Selecionada

**Por quê:** Maximiza reuso do código existente, entrega valor incremental por sprint e cobre todas as plataformas sem reescrever infraestrutura.

**Sprint 1 — Ativar (sem nova infra crítica)**
- Candidatos discovery: script manual → dim_candidato_social_pages → social_ingest lê BQ dinamicamente
- Instagram: `fetch_instagram_posts()` com mesmo META_APP_TOKEN
- Google Trends: mover de digital_ingest para social_ingest
- GDELT: reabilitar com tenacity backoff + cache GCS 30min

**Sprint 2 — Elevar sentimento**
- Vertex AI NLP obrigatório em prod (`enrich_sentiment_vertex` já existe)
- Novos campos Silver: `sentimento_score` (float -1 a +1), `confianca_nlp` (float 0-1), `temas` (array string)
- Rule-based: fallback apenas local (sem GCP_PROJECT_ID)

**Sprint 3 — Scheduling híbrido**
- Cloud Scheduler: RSS + Google Trends → 1×/dia (23h)
- Cloud Scheduler: YouTube + Facebook + Instagram → 4×/dia (0h, 6h, 12h, 18h)
- Cloud Scheduler: Bluesky + Reddit → 2×/dia (8h, 20h)
- Twitter/X: 1×/dia apenas se budget aprovado (`SOCIAL_X_ENABLED=true`)

**Sprint 4 — Crise + TikTok**
- Vigilante agent: volume > 2× média histórica UF → publicar em `drift-detected`
- TikTok: solicitar Research API + implementar `tiktok_client.py`

---

### Approach B: Full Streaming — Não selecionada

Pub/Sub + Dataflow para todas as plataformas em tempo real.
**Por que não:** Custo Dataflow ~$0.08/vCPU-hora injustificado. Cloud Scheduler 4×/dia entrega quasi-tempo-real suficiente.

---

### Approach C: News-First — Não selecionada

Priorizar RSS score alto + GDELT, despriorizar social puro.
**Por que não:** Perde sinal direto de percepção popular. RSS já está ativo — mais feeds de imprensa não agrega valor marginal para os objetivos do módulo.

---

## YAGNI — Features Removidas do Escopo v1.2

| Feature | Motivo |
|---------|--------|
| Telegram | Sem API oficial; risco de violação de ToS |
| Dataflow streaming completo | Custo injustificado; Cloud Run + Pub/Sub suficiente |
| **Twitter/X 4×/dia** | **Custo proibitivo — Basic API $100/mês; free tier insuficiente para produto. 1×/dia apenas se budget aprovado** |
| Análise de imagens/vídeo | Vertex AI Vision — custo alto; defer v2.0 |
| Instagram Stories scraping avançado | Graph API avançada; complexidade sem ROI claro |

---

## Feature: Candidatos Discovery

### Problema
Lista de candidatos 2026 ainda não existe formalmente (eleições futuras). O módulo social precisa saber quais páginas monitorar.

### Solução Híbrida (aprovada pelo usuário)

**Parte 1 — Script manual (primeira execução):**
```
fact_pesquisa Silver (BQ 2026) ──► candidatos com ≥3 menções em polls
TSE candidaturas 2022          ──► match por nome → nome oficial
          │
          ▼
Facebook Graph: GET /search?q={nome}&type=page
          │
          ├── score: followers + is_verified + bio_keywords
          └── output: CSV para revisão humana → aprovação → Terraform
```

**Parte 2 — Job mensal (automático):**
- `candidatos_discovery_job.py` → Cloud Scheduler 1×/mês
- Query novos candidatos em pesquisas (threshold: ≥3 menções)
- Atualiza `spepe_silver.dim_candidato_social_pages` no BigQuery
- `social_ingest_job.py` lê a tabela dinamicamente (não mais lista estática)

### Schema: dim_candidato_social_pages

| Campo | Tipo | Descrição |
|-------|------|-----------|
| candidato_id | STRING | ID interno SPEPE |
| nome_candidato | STRING | Nome oficial TSE |
| facebook_page_id | STRING | ID da página verificada |
| instagram_handle | STRING | Handle Instagram |
| youtube_channel_id | STRING | Channel ID YouTube |
| twitter_handle | STRING | @handle (budget-gated) |
| tiktok_handle | STRING | Handle TikTok (futuro) |
| followers_fb | INT64 | Seguidores no momento da descoberta |
| is_verified | BOOL | Página verificada pela plataforma |
| dt_atualizacao | DATE | Última atualização |

---

## Validações Realizadas

### Validação 1 — Approach A + ordem dos sprints
Sprint 1 (ativar) → Sprint 2 (sentimento) → Sprint 3 (scheduling) → Sprint 4 (crise + TikTok). ✅ Confirmado.

### Validação 2 — Escopo produto (não MVP)
TikTok entra no v1.2, Instagram entra, streaming de crise entra, Vertex AI NLP obrigatório, GDELT reabilitar. ✅ Confirmado: "não estamos no MVP, e sim produto".

---

## Requisitos Sugeridos para /define

### Funcionais
1. Ingerir menções de candidatos: Facebook, Instagram, YouTube, Bluesky, RSS (8 feeds), Google Trends, Reddit
2. Sentimento via Vertex AI NLP: `sentimento_score` (-1 a +1), `confianca_nlp` (0-1), `temas` (array)
3. Descoberta automática de páginas via `fact_pesquisa 2026` + Facebook Graph search
4. Tabela `dim_candidato_social_pages` em BigQuery com handles por plataforma
5. `social_ingest_job.py` lê candidatos dinamicamente do BigQuery (não hardcoded)
6. Scheduling híbrido: RSS/Trends 1×/dia; YouTube/Facebook/Instagram 4×/dia; Bluesky/Reddit 2×/dia
7. Detecção de crise: volume > 2× média histórica por UF → publicar em `drift-detected`
8. GDELT reabilitado com backoff exponencial + cache GCS 30min
9. TikTok: solicitar Research API + implementar `tiktok_client.py` quando aprovado
10. Twitter/X budget-gated: ativar 1×/dia apenas se `SOCIAL_X_ENABLED=true` (env var explícita)
11. **Detecção de desinformação para DQ:** flagrar posts com padrão de coordenação (volume anormal + contas novas/sem histórico) com campo `suspeito_coordenado` (bool) + `score_credibilidade_post` (float) — posts suspeitos entram no Gold com peso reduzido, não são removidos
12. Janelas de lookback por fonte: Twitter/X=7d, Bluesky=7d, RSS=30d, YouTube=configurável, Facebook=sem limite fixo, Google Trends=histórico completo — Bronze retém rolling 90d

### Não-Funcionais

| Requisito | Meta |
|-----------|------|
| Latência máxima para detecção de crise | < 4 horas |
| Cobertura mínima de plataformas ativas | ≥ 6 simultâneas |
| Score mínimo de confiança NLP para Gold | ≥ 0.70 |
| Custo incremental mensal | < $50 (excluindo Twitter/X) |
| Disponibilidade do social_ingest_job | > 99% (retry automático) |
| Retenção Bronze social | Rolling 90 dias |

### Fora do Escopo v1.2
- Telegram (sem API oficial)
- Análise de imagens e vídeo (Vertex AI Vision) — defer v2.0
- Dataflow streaming completo — Cloud Scheduler 4×/dia suficiente
- Twitter/X como fonte primária — custo proibitivo
- Instagram Stories scraping avançado
- Moderação ou remoção de conteúdo — não é responsabilidade do sistema
- Histórico retroativo > 30 dias via API social — limite das APIs (Twitter/X: 7d; Bluesky: 7d; RSS: ~30d)

---

## Próxima Fase

```bash
/define .claude/sdd/features/BRAINSTORM_REDES_SOCIAIS_V12.md
```

O `/define` deve capturar:
- Histórias de usuário por camada (narrativa / sentimento / crise)
- Critérios de aceite por plataforma e por sprint
- SLAs de scheduling e latência
- Contrato de dados da `dim_candidato_social_pages`
- Critério de aceite do `candidatos_discovery_job`
- Definição de "crise detectada" (threshold, janela temporal, UFs afetadas)

---

## Revision History

| Versão | Data | Autor | Mudanças |
|--------|------|-------|---------|
| 1.0 | 2026-05-07 | brainstorm-agent | Brainstorm inicial — 3 perguntas, 2 validações, Approach A selecionada. Twitter/X reclassificado como secundário (custo Basic API $100/mês). Candidatos Discovery híbrido aprovado (script + job mensal). Escopo: produto. |
