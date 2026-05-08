# SPEPE — Status Técnico
**Data:** 2026-05-06 | **Versão:** v1.1 | **Commits recentes:** feat(sancoes), feat(emendas), feat(cadunico)

---

## 1. Estado Atual — 13 Módulos + 19 Cloud Run Jobs

### Resumo Executivo

| # | Módulo | % | Status |
|---|--------|---|--------|
| 1 | Eleições TSE | 85% | Bronze 27 UFs ingerido em spepe-prod; Silver/Gold ativo |
| 2 | IBGE / Dados Públicos | 75% | Silver BQ ativo; join TSE↔IBGE 97-99% match |
| 3 | Pesquisas Eleitorais | 70% | Silver/Gold pesquisas wired; multi-ano 2018/2022/2026 |
| 4 | Redes Sociais | 40% | Sentimento stub; YouTube/TikTok ausentes; **módulo pendente** |
| 5 | Segurança Pública | 65% | Bronze ativo; Silver/Gold em silver_transform_job |
| 6 | Saúde (DataSUS) | 55% | Bronze ativo; Silver/Gold em silver_transform_job |
| 7 | Economia (DIEESE/CETIC) | 60% | Bronze ativo; Silver/Gold em silver_transform_job |
| 8 | MLOps / Predição | 65% | Pipeline KFP compilado; Gold insuficiente para treino completo |
| 9 | Agentes / UI | 75% | 8 agentes roteados; dashboard 4 abas ativas |
| 10 | Infra / Segurança | 90% | 19 jobs no Terraform + deploy.yml; TRANSPARENCIA_API_KEY em SM |
| 11 | **Transferências Sociais (CadÚnico/BF)** | 80% | Bronze 2018/2022/2024/2025; Silver rodando; Gold fact_transferencias_sociais ✅ |
| 12 | **Emendas Parlamentares** | 60% | Client + Job criados; Bronze a rodar; Silver/Gold pendentes |
| 13 | **Ficha Suja (CEIS+CNEP)** | 60% | Client + Job criados; Bronze a rodar; Silver/Gold pendentes |

---

## 2. Cloud Run Jobs — 19 total em spepe-prod

| Job | Criado em prod | Último run | Status |
|-----|---------------|------------|--------|
| spepe-tse-ingest | ✅ | 2026-05 | ✅ |
| spepe-tse-perfil-ingest | ✅ | 2026-05 | ✅ |
| spepe-tse-candidaturas-ingest | ✅ | 2026-05 | ✅ |
| spepe-ibge-sync | ✅ | 2026-05 | ✅ |
| spepe-digital-ingest | ✅ | 2026-05 | ✅ |
| spepe-social-ingest | ✅ | 2026-05 | ✅ |
| spepe-pesquisas-ingest | ✅ | 2026-05 | ✅ |
| spepe-security-ingest | ✅ | 2026-05 | ✅ |
| spepe-datasus-ingest | ✅ | 2026-05 | ✅ |
| spepe-dieese-ingest | ✅ | 2026-05 | ✅ |
| spepe-cetic-ingest | ✅ | 2026-05 | ✅ |
| spepe-reddit-ingest | ✅ | 2026-05 | ✅ |
| spepe-camara-senado-ingest | ✅ | 2026-05 | ✅ |
| spepe-endividamento-ingest | ✅ | 2026-05 | ✅ |
| spepe-cadunico-ingest | ✅ | 2026-05-06 | ✅ Bronze 4 anos |
| spepe-emendas-ingest | ⏳ CI build pending | — | Job criado via Terraform/CI |
| spepe-sancoes-ingest | ⏳ CI build pending | — | Job criado via Terraform/CI |
| spepe-silver-transform | ✅ | 2026-05-06 | 🔄 Rodando (CadÚnico Silver) |
| spepe-gold-build | ✅ | 2026-05 | ✅ |

---

## 3. Bronze Coverage — spepe-prod GCS

| Fonte | Anos/Escopo | UFs | Status |
|-------|-------------|-----|--------|
| TSE Resultados | 2018, 2022 | 27 | ✅ |
| TSE Perfil Eleitorado | 2018, 2022 | 27 | ✅ |
| TSE Candidaturas | 2018, 2022 | 27 | ✅ |
| TSE Pesquisas | 2018, 2022, 2026 | BR | ✅ |
| IBGE | Censo 2022, PNAD | 27 | ✅ |
| Segurança | 2022, 2023 | 27 | ✅ |
| DataSUS | 2022 | 27 | ✅ (2023 pendente) |
| DIEESE/CETIC | 2022 | BR | ✅ |
| Reddit | últimos 30d | BR | ✅ |
| Câmara/Senado | Legislatura 57 | BR | ✅ |
| Endividamento | 2025–2026 | 27 | ✅ |
| CadÚnico/BF | 2018, 2022, 2024, 2025 | BR | ✅ |
| Emendas Parlamentares | 2022 | BR | ⏳ aguarda novo CI build |
| CEIS+CNEP Sanções | Histórico completo | BR | ⏳ aguarda novo CI build |

---

## 4. Silver / Gold — BigQuery spepe-prod

### Silver (spepe_silver)

| Tabela | Status | Observações |
|--------|--------|-------------|
| tse_{uf}_{year} | ✅ | 27 UFs × 2018/2022 |
| ibge_* | ✅ | múltiplas tabelas |
| pesquisas | ✅ | multi-ano |
| seguranca_* | ✅ | |
| saude_* | ✅ | |
| economia_* | ✅ | |
| social_* | ✅ (vazio sem ingestão) | |
| **transferencias_sociais** | 🔄 Silver rodando | 4 anos CadÚnico |
| emendas_parlamentares | ⏳ Silver pendente | |
| sancoes_federais | ⏳ Silver pendente | |

### Gold (spepe_gold)

| Tabela | Status |
|--------|--------|
| fact_municipio_eleicao | ✅ |
| fact_candidato_eleicao | ✅ |
| fact_ibge_municipio | ✅ |
| fact_saude_municipio | ✅ |
| fact_seguranca_municipio | ✅ |
| fact_economico_municipio | ✅ |
| fact_pesquisa | ✅ |
| **fact_transferencias_sociais** | ⏳ Gold build após Silver |
| fact_emendas_parlamentares | ⏳ Silver pendente |
| fact_sancoes_federais | ⏳ Silver pendente |

---

## 5. Próximos Passos

1. **Aguardar Silver transform atual** (`spepe-silver-transform-wcfct`) — CadÚnico → `transferencias_sociais`
2. **Dispatchar Gold build** → gera `fact_transferencias_sociais`
3. **CI build do commit sancoes** → criar `spepe-emendas-ingest` e `spepe-sancoes-ingest` em prod
4. **Rodar emendas_ingest**: anos 2018, 2022, 2025
5. **Rodar sancoes_ingest**: snapshot histórico completo CEIS+CNEP
6. **Silver + Gold emendas e sanções**: implementar `transform_emendas_to_silver()` e `transform_sancoes_to_silver()`
7. **Módulo Redes Sociais**: Twitter/X sentiment, YouTube, TikTok — módulo #4 pendente principal

---

## 6. Análises Disponíveis — "Por que o eleitor vota assim?"

Com os dados atuais já ingeridos, as seguintes análises são possíveis:

| Pergunta analítica | Dados disponíveis | Tabela Gold |
|--------------------|-------------------|-------------|
| Voto × escolaridade/renda | TSE + IBGE | fact_municipio_eleicao |
| Voto × Bolsa Família | TSE + CadÚnico/BF | fact_transferencias_sociais × fact_municipio_eleicao |
| Voto × emendas parlamentares | TSE + Emendas | fact_emendas_parlamentares × fact_municipio_eleicao |
| Candidatos "ficha suja" × resultado | TSE + CEIS/CNEP | fact_sancoes_federais × fact_candidato_eleicao |
| Voto × violência | TSE + Segurança | fact_seguranca_municipio |
| Voto × saúde pública | TSE + DataSUS | fact_saude_municipio |
| Intenção de voto × resultado | Pesquisas + TSE | fact_pesquisa × fact_municipio_eleicao |
