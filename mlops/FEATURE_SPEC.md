# SPEPE — Feature Specification do Modelo Eleitoral

## Arquitetura de Features

### Bloco 1 — Estrutural (IBGE, estável)
| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| log_populacao | IBGE Censo | fact_ibge_municipio | alto — municípios maiores têm padrões distintos | 1.0 |
| log_renda | IBGE SIDRA | fact_ibge_municipio | alto — renda determina perfil de voto | 1.0 |
| taxa_alfabetizacao | IBGE | fact_ibge_municipio | médio — correlacionado com renda | 0.3 |
| taxa_analfabetismo | IBGE | fact_ibge_municipio | médio — inverso da alfabetização | 0.3 |

### Bloco 2 — Sinal Eleitoral (TSE + Pesquisas)
| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma |
|---------|-------|-----------|-----------------|-------------|
| pct_votos_historico | TSE | fact_municipio_candidato_eleicao | MUITO alto (~30%) — melhor predictor | via w_hist |
| media_intencao_voto | Pesquisas | fact_pesquisa_eleitoral | alto (~25%) — nível atual de intenção | via w_poll |
| delta_poll | Pesquisas (computed) | — | médio — candidato em alta ou queda | 0.5 |
| is_new_candidate | TSE (computed) | — | binário — candidatos novos sem histórico | — |
| poll_missing | — (computed) | — | flag — quando sem pesquisa disponível | — |

### Bloco 3 — Dinâmicas Externas (Social + GDELT + Tendências) ← NOVO
| Feature | Fonte | Tabela BQ | Impacto esperado | Prior sigma | Disponível |
|---------|-------|-----------|-----------------|-------------|-----------|
| sentimento_score | Twitter/X, BlueSky, FB | spepe_silver.social_mencoes_br | médio — sentiment shift precede pesquisas em 1-2 semanas | 0.5 | parcial |
| tendencia_busca | Google Trends | spepe_gold.fact_google_trends_uf | médio-baixo — interesse correlaciona com voto em cargos nacionais | 0.3 | sim |
| gdelt_intensidade | GDELT | spepe_silver.gdelt_events | médio — volume de eventos políticos por candidato | 0.4 | parcial |
| gdelt_tom | GDELT | spepe_silver.gdelt_events | médio — tonalidade dos eventos (positivo/negativo) | 0.4 | parcial |
| reputacao_score | computed | — | DIFERENCIAL — captura "casos de repercussão" antes das pesquisas | 0.6 | computado |

## Fórmula do reputacao_score

```python
# reputacao_score ∈ [-1, 1]
# Inputs: sentimento_score ∈ [-1,1], gdelt_tom ∈ [-1,1], tendencia_norm ∈ [-1,1]
# Pesos: sentimento (0.5), gdelt_tom (0.3), tendencia (0.2)
reputacao_score = 0.5 * sentimento_score + 0.3 * gdelt_tom + 0.2 * tendencia_norm
```

## Estratégia de Impacto — "Casos de Repercussão"

Um escândalo (ex: CPI, operação policial, revelação de mídia) gera este padrão:
1. **T+0**: Evento reportado → gdelt_intensidade ↑↑, gdelt_tom ↓
2. **T+1 semana**: Redes sociais amplificam → sentimento_score ↓, tendencia_busca ↑
3. **T+2-3 semanas**: Pesquisas convencionais captam → media_intencao_voto ↓
4. **T+4-6 semanas**: Resultado eleitoral se move

**Vantagem SPEPE**: Ao monitorar `reputacao_score` em tempo real, detectamos mudanças 2-3 semanas antes das pesquisas convencionais.

## Gestão de Dados Faltantes

As features dinâmicas estão disponíveis apenas para eleições recentes e quando os tokens de API estão configurados:

| Cenário | Tratamento |
|---------|-----------|
| sentimento sem tokens | 0.0 (neutro) |
| GDELT sem dados para candidato | 0.0 |
| Google Trends sem dados | média UF |
| delta_poll sem pesquisa anterior | 0.0 + flag poll_missing=True |

## Evolução do Modelo

| Versão | Features | Brier esperado |
|--------|----------|---------------|
| M1 — baseline demográfico | 4 IBGE | ~0.25 |
| M2 — eleitoral completo | +hist +polls | ~0.10-0.18 |
| M3 — com dinâmicas externas | +sentiment +GDELT +trends | ~0.08-0.14 (estimado) |

M3 ainda não treinado — requer dados sociais completos (Fase 2).
