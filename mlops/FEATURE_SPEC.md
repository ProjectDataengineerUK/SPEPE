# SPEPE — Feature Specification do Modelo Eleitoral

**Atualizado:** 2026-05-21
**Revisão:** v2.0 — separação Governador (M-G) e Deputado Federal (M-F) conforme análise eleitoral RJ 2026

## Arquitetura de Modelos

```
Modelo M-G: Governador (majoritário, 1º/2º turno)
  → Entrada: pesquisas + histórico + rejeição + território + apoios + social
  → Saída:   pct_1t, prob_2t, pct_2t_cenario_AB, rejeição_tendência

Modelo M-F: Deputado Federal (proporcional, lista aberta, D'Hondt)
  → Entrada: voto histórico individual + nominata + apoios locais + concentração
  → Saída:   votos_estimados, cadeiras_partido, prob_eleicao, risco_fora_da_lista

Fontes compartilhadas: TSE, IBGE, redes sociais, prefeitos, notícias
```

---

## Modelo M-G — Governador

### Bloco G1 — Pesquisas de Opinião (peso dominante ~45%)

| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| media_intencao_estimulada | PesqEle/TSE | fact_pesquisa_eleitoral | MUITO alto — estado atual da corrida | via w_poll |
| media_intencao_espontanea | PesqEle/TSE | fact_pesquisa_eleitoral | alto — recall real sem sugestão | 0.5 |
| taxa_rejeicao | PesqEle/TSE | fact_pesquisa_eleitoral | **CRITICO** — define teto de crescimento | 0.8 |
| potencial_teorico | computed | — | = 100 - taxa_rejeicao; teto máximo teórico | — |
| saldo_intencao_rejeicao | computed | — | = intenção_estimulada - rejeição; sinal líquido | — |
| pct_indecisos | PesqEle/TSE | fact_pesquisa_eleitoral | médio — espaço disponível | 0.3 |
| pct_2t_principal_adversario | PesqEle/TSE | fact_pesquisa_eleitoral | alto — cenário mais provável de 2º turno | via w_poll |
| delta_poll_30d | computed | — | tendência: candidato subindo ou caindo | 0.5 |
| poll_missing | computed | — | flag — sem pesquisa disponível | — |

### Bloco G2 — Histórico Eleitoral (peso ~20%)

| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| pct_votos_historico_gov | TSE 2022 | fact_municipio_candidato_eleicao | alto — base comprovada em eleição anterior | via w_hist |
| pct_votos_presidencial_2022 | TSE 2022 | fact_municipio_candidato_eleicao | alto — proxy bolsonarismo/lulismo por município | 0.6 |
| pct_cláudio_castro_2022 | TSE 2022 | fact_municipio_candidato_eleicao | médio — força do campo PL/direita no estado | 0.5 |
| is_incumbent | TSE (computed) | — | binário — incumbente governador tem vantagem | — |
| is_new_candidate | computed | — | binário — sem histórico estadual | — |

### Bloco G3 — Território RJ (peso ~15%)

Regiões recomendadas para o RJ (8 regiões padrão SPEPE-RJ):

| Região | Municípios principais | Peso eleitoral aproximado |
|--------|----------------------|--------------------------|
| Capital | Rio de Janeiro | ~35% dos eleitores |
| Baixada Fluminense | Duque de Caxias, Nova Iguaçu, Belford Roxo, São João de Meriti | ~20% |
| Leste Metropolitano | Niterói, São Gonçalo, Itaboraí, Maricá | ~12% |
| Norte Fluminense | Campos, Macaé, São João da Barra | ~8% |
| Noroeste Fluminense | Itaperuna, Santo Antônio de Pádua | ~4% |
| Região Serrana | Petrópolis, Teresópolis, Nova Friburgo | ~5% |
| Sul Fluminense | Volta Redonda, Barra Mansa, Resende, Valença | ~6% |
| Costa Verde | Angra, Paraty, Mangaratiba | ~2% |

| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| forca_capital | TSE + pesquisa | fact_municipio_candidato_eleicao | muito alto — 35% do eleitorado | via beta_regiao |
| forca_baixada | TSE + apoios | fact_municipio_candidato_eleicao | alto — 20% do eleitorado | via beta_regiao |
| forca_norte_fluminense | TSE + apoios | fact_municipio_candidato_eleicao | médio — base Garotinho/PL | via beta_regiao |
| forca_leste_metropolitano | TSE + apoios | fact_municipio_candidato_eleicao | médio — base PDT/Cidadania | via beta_regiao |
| forca_interior | TSE (aggregated) | fact_municipio_candidato_eleicao | médio — regiões Serrana/Sul/Costa Verde | via beta_regiao |

### Bloco G4 — Apoios Políticos (peso ~12%)

| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| apoios_prefeitos_ponderados | dim_precandidato_2026 + TSE eleitores | dim_precandidato_2026 | alto — prefeito × eleitorado do município | 0.6 |
| eleitores_cobertos_por_prefeitos | computed | — | = sum(eleitores_município para cada prefeito apoiador) | 0.5 |
| pct_eleitores_estado_cobertos | computed | — | = eleitores_cobertos / total_eleitores_RJ | 0.5 |
| campo_politico_fragmentado | computed | — | = 1 quando campo adversário >2 candidatos competitivos | 0.4 |
| grau_confirmacao_apoio | dim_precandidato_2026 | dim_precandidato_2026 | médio — qualidade do apoio (declarado vs bastidor) | 0.3 |

Classificação do grau de apoio para ponderar:

| Grau | Multiplicador | Exemplo |
|------|--------------|---------|
| declarado | 1.0 | prefeito declarou apoio em evento |
| evento_publico | 0.7 | apareceu na filiação/lançamento |
| partidario | 0.5 | mesmo partido/federação |
| bastidor | 0.2 | noticiado, sem declaração |

### Bloco G5 — Dinâmicas Externas + Risco (peso ~8%)

| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| reputacao_score | computed (social+GDELT+trends) | spepe_gold.fact_social_mencoes | médio — sinal precede pesquisas 2-3 semanas | 0.5 |
| sentimento_score | social_client | spepe_silver.social_mencoes_br | médio — tendência antes de pesquisas | 0.4 |
| tendencia_busca | Google Trends | fact_google_trends_uf | médio-baixo — interesse vs intenção | 0.3 |
| cobertura_midia_log | GDELT + RSS | spepe_silver.gdelt_events | médio — amplifica eventos negativos | 0.4 |
| risco_juridico | dim_precandidato_2026 | dim_precandidato_2026 | binário — inelegibilidade potencial | — |

### Bloco G6 — Estrutural IBGE (peso ~8%)

| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| log_populacao | IBGE Censo | fact_ibge_municipio | médio — tamanho do município | 1.0 |
| log_renda | IBGE SIDRA | fact_ibge_municipio | médio — perfil socioeconômico | 1.0 |
| taxa_alfabetizacao | IBGE | fact_ibge_municipio | baixo (mediado por renda) | 0.3 |
| urbanizacao_pct | IBGE | fact_ibge_municipio | baixo | 0.3 |

---

## Modelo M-F — Deputado Federal (Proporcional)

O model M-F tem uma estrutura diferente: além de prever votos individuais,
precisa simular o **quociente eleitoral** (D'Hondt) para estimar cadeiras.

```
M-F fluxo:
  1. Prever votos de cada candidato (Gradient Boosting ou PyMC individual)
  2. Prever votos totais de cada partido/federação (soma + legenda)
  3. Calcular quociente eleitoral: total_validos / 46 cadeiras
  4. Simular D'Hondt: cadeiras por partido
  5. Ordenar candidatos dentro do partido por votos
  6. Derivar probabilidade de eleição de cada candidato
```

### Bloco F1 — Voto Histórico Individual (peso ~30%)

| Feature | Fonte | Tabela BQ | Impacto esperado |
|---------|-------|-----------|-----------------|
| votos_historico_2022 | TSE | fact_candidato_eleicao | MUITO alto — melhor predictor para federal |
| votos_historico_2018 | TSE | fact_candidato_eleicao | alto — trend de 2 ciclos |
| delta_votos_1822 | computed | — | = votos_2022 - votos_2018; crescimento ou queda |
| voto_familiar_2022 | computed | — | votos do grupo político/família (Garotinho cluster) |
| incumbente_federal | TSE (computed) | — | binário — incumbente tem vantagem estrutural ~20% |
| cargos_anteriores | TSE | dim_candidato | médio — histórico de cargos executivos/legislativos |
| pct_votos_municipio_base | TSE | fact_municipio_candidato_eleicao | alto — % no município principal |

### Bloco F2 — Nominata e Partido (peso ~25%)

| Feature | Fonte | Tabela BQ | Impacto esperado |
|---------|-------|-----------|-----------------|
| votos_partido_federacao_2022 | TSE | fact_partido_eleicao | MUITO alto — legenda puxa ou afunda candidatos |
| puxadores_na_nominata | computed | — | flag: nominata tem candidato com >100k votos esperados |
| candidatos_competitivos_nominata | computed | — | count de candidatos com >30k votos esperados |
| delta_votos_partido_1822 | computed | — | partido cresceu ou caiu entre ciclos |
| federacao_ativa | TSE | dim_partido_federacao | binário — partido em federação tem mais votos de legenda |
| posicao_estimada_nominata | computed | — | rank do candidato dentro do partido (por votos históricos) |
| fundo_eleitoral_recebido | TSE prestação de contas | — | alto — financiamento direciona campanha |

### Bloco F3 — Concentração e Território (peso ~20%)

| Feature | Fonte | Tabela BQ | Impacto esperado |
|---------|-------|-----------|-----------------|
| indice_concentracao_top5 | computed | — | % dos votos esperados nos 5 maiores municípios; risco de dependência |
| municipio_base_principal | TSE | fact_municipio_candidato_eleicao | alto — município onde candidato é dominante |
| eleitores_municipio_base | TSE | dim_municipio | médio — tamanho da base principal |
| regiao_rj | computed | — | das 8 regiões padrão; Norte Fluminense = Garotinho |
| capilaridade_municipios | computed | — | count de municípios onde candidato teve >1000 votos em 2022 |

### Bloco F4 — Apoios Locais (peso ~15%)

| Feature | Fonte | Tabela BQ | Impacto esperado |
|---------|-------|-----------|-----------------|
| prefeitos_apoiadores_count | dim_precandidato_2026 | dim_precandidato_2026 | alto — cada prefeito traz estrutura local |
| eleitores_cobertos_prefeitos | computed | — | soma dos eleitores dos municípios dos prefeitos apoiadores |
| vereadores_estimados | computed | — | proxy = votos_vereadores_aliados (quando disponível) |
| dep_estaduais_aliados_votos | TSE | fact_candidato_eleicao | médio — rede estadual |
| apoio_lideranca_nacional | dim_precandidato_2026 | dim_precandidato_2026 | médio — Flávio Bolsonaro/Quaquá/etc. |

### Bloco F5 — Digital e Perfil Público (peso ~8%)

| Feature | Fonte | Tabela BQ | Impacto esperado |
|---------|-------|-----------|-----------------|
| ig_followers | fact_precandidato_profile_history | dim_precandidato_2026 | médio-alto — Gracyanne Barbosa efeito; alto engajamento digital |
| x_followers | fact_precandidato_profile_history | dim_precandidato_2026 | médio |
| yt_subscribers | fact_precandidato_profile_history | dim_precandidato_2026 | médio |
| crescimento_seguidores_30d | computed | — | momentum digital |
| influenciador_flag | computed | — | binário — candidato com >500k seguidores totais antes das eleições |

### Bloco F6 — Risco e Segurança Jurídica (peso ~2%)

| Feature | Fonte | Tabela BQ | Impacto esperado |
|---------|-------|-----------|-----------------|
| risco_juridico_flag | dim_precandidato_2026 | dim_precandidato_2026 | binário — impede ou reduz votos |
| status_confirmado | dim_precandidato_2026 | dim_precandidato_2026 | binário — pré-cand confirmado vs citado |

---

## Simulador D'Hondt — Quociente Eleitoral

Para o Modelo M-F, o módulo `mlops/dhondt_simulator.py` deve:

```python
# Entradas
votos_por_candidato: dict[str, int]         # candidato → votos estimados
partido_por_candidato: dict[str, str]       # candidato → partido/federação
n_cadeiras_rj: int = 46                     # deputados federais pelo RJ

# Fluxo
1. Agregar votos por partido/federação
2. Calcular quociente eleitoral = votos_validos_totais / n_cadeiras
3. Calcular cadeiras diretas: floor(votos_partido / quociente)
4. Distribuir sobras (maior resto)
5. Ordenar candidatos dentro de cada partido por votos (descendente)
6. Candidatos até o número de cadeiras do partido = eleitos

# Saídas
cadeiras_por_partido: dict[str, int]
eleitos: list[str]                          # candidatos eleitos
probabilidade_eleicao: dict[str, float]     # via Monte Carlo com N simulações
```

---

## Fórmulas Derivadas Críticas

### Para Governador

```python
# Teto teórico de crescimento
potencial_teorico = 100 - taxa_rejeicao

# Saldo líquido (quanto a rejeição come da intenção)
saldo = media_intencao_estimulada - taxa_rejeicao
# Paes: ~42% - 18% = +24 (saudável)
# Garotinho: ~14% - 46% = -32 (teto muito baixo)

# Prefeitos ponderados por eleitorado
apoios_pond = sum(eleitores_municipio * multiplicador_grau_apoio
                  for prefeito in prefeitos_apoiadores)

# Bolsonarismo fragmentado (aumenta chance de Paes)
campo_fragmentado = 1 if count(candidatos_bolsonaristas) >= 3 else 0
```

### Para Deputado Federal

```python
# Concentração territorial (risco de dependência)
indice_concentracao = votos_top5_municipios / votos_totais_candidato
# > 0.7 = risco alto (base muito concentrada)

# Score de viabilidade composto (pré-modelo)
score_viab = (
    0.25 * normalize(votos_historico_2022) +
    0.25 * normalize(forca_nominata) +
    0.20 * normalize(eleitores_cobertos_prefeitos) +
    0.15 * normalize(capilaridade_municipios) +
    0.10 * normalize(ig_followers + yt_subscribers) +
    0.05 * (1 - risco_juridico_flag)
)
```

---

## Reputacao Score (mantido de v1.0)

```python
# reputacao_score ∈ [-1, 1]
base = 0.40 * sentimento_score + 0.25 * gdelt_tom + 0.20 * tendencia_norm
amplifier = 1.0 + 0.15 * abs(cobertura_norm)
reputacao_score = clip(base * amplifier, -1, 1)
```

---

## Gestão de Dados Faltantes

| Feature | Cenário | Fallback |
|---------|---------|----------|
| taxa_rejeicao | sem pesquisa | 0.30 (prior neutra) + flag rejeicao_missing |
| media_intencao_estimulada | sem pesquisa | 0.0 + flag poll_missing |
| votos_historico_2022 | candidato novo | 0 + is_new_candidate=1 |
| apoios_prefeitos | sem mapeamento | 0 (score zero) |
| ig_followers | sem token | 0 + flag social_missing |
| forca_nominata | partido novo | média histórica do partido |
| risco_juridico_flag | sem processo identificado | 0 |

---

## Evolução dos Modelos

| Versão | Modelo | Features | Brier esperado (Gov) | Precisão Fed |
|--------|--------|----------|---------------------|-------------|
| M1 | baseline demográfico | 4 IBGE | ~0.25 | — |
| M2 | eleitoral completo (atual) | +hist +polls +social | ~0.10–0.18 | — |
| M-G v1 | governador dedicado | +rejeição +território +apoios | ~0.08–0.14 | — |
| M-F v1 | deputado federal | +nominata +D'Hondt +concentração | — | acerto top-20: ~65% |
| M-G v2 | com social completo | +sentimento real-time | ~0.06–0.10 | — |
| M-F v2 | com financiamento | +prestação contas TSE | — | acerto top-20: ~75% |

M-G v1 e M-F v1 = próxima iteração pós-execução do M2 em GCP.

---

## Estratégia de Impacto — Eventos de Repercussão (mantida)

Um escândalo gera este padrão temporal:
1. **T+0**: Evento → gdelt_intensidade↑↑, gdelt_tom↓
2. **T+1 semana**: Redes amplificam → sentimento_score↓, tendencia_busca↑
3. **T+2-3 semanas**: Pesquisas captam → media_intencao_voto↓ (ou rejeição↑)
4. **T+4-6 semanas**: Resultado eleitoral se move

**Vantagem SPEPE**: detecta mudanças 2-3 semanas antes de pesquisas convencionais via `reputacao_score`.

---

## Fontes de Dados — Tabela de Referência

| Fonte | Uso Principal | Modelos |
|-------|--------------|---------|
| TSE PesqEle | pesquisas + rejeição | M-G |
| TSE Dados Abertos (resultados) | histórico municipal/zona | M-G, M-F |
| TSE DivulgaCandContas | candidaturas + financiamento | M-F |
| IBGE Cidades | dados socioeconômicos | M-G, M-F |
| dim_precandidato_2026 | apoios + perfis + risco jurídico | M-G, M-F |
| fact_precandidato_profile_history | métricas de perfil social | M-G, M-F |
| spepe_silver.social_mencoes_br | sentimento + cobertura | M-G |
| Base dos Dados (br_tse_eleicoes) | histórico tratado via BQ/SQL | M-F |
