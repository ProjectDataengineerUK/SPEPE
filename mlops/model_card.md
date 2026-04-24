# Model Card: SPEPE Electoral Prediction Model

## Model Overview

| Attribute | Value |
|-----------|-------|
| **Name** | spepe-electoral-model |
| **Version** | 1.0 (MVP) |
| **Type** | Binary Classification — Logistic Regression + Bootstrap IC 95% |
| **Task** | Predict P(candidate wins municipality) with credibility interval |
| **Production path** | PyMC Hierarchical Logistic Model (see `mlops/pymc_model.py`) |

## Training Data

| Dataset | Source | Coverage | Period |
|---------|--------|----------|--------|
| `fact_municipio_eleicao` | TSE + IBGE join | 5.570 municípios | 2014, 2018, 2022 |
| Features used | IDHM, renda, escolaridade, % rural, PIB per capita, histórico eleitoral | ~50 features (MVP) | - |

## Performance Metrics (Backtesting 2018→2022)

| Metric | Value | Interpretation |
|--------|-------|----------------|
| Accuracy | TBD after first run | % municípios com resultado correto |
| Brier Score | TBD | Calibração probabilística (< 0.25 = bom) |
| MAE | TBD | Erro médio absoluto em probabilidade |

*Métricas serão preenchidas após a primeira execução do pipeline Vertex AI.*

## Limitations

1. **Escopo geográfico limitado**: MVP validado apenas para SP. Generalização para outros estados requer retrain.
2. **Dados históricos estáticos**: Não incorpora eventos de campanha pós-data de corte.
3. **Granularidade municipal**: Previsões por município, não por zona ou seção.
4. **Bootstrap ≠ Bayesiano**: IC 95% via bootstrap é aproximação. PyMC HLM em produção.
5. **Sinal digital parcial**: Trends e Meta Ads agregados podem subestimar heterogeneidade intra-municipal.

## Ethical Considerations

- Todas as previsões incluem disclaimers obrigatórios de limitação metodológica.
- Dados sempre em nível agregado (município) — LGPD compliant.
- Não deve ser usado para suprimir participação eleitoral ou criar narrativas de derrota antecipada.
- Correlações observadas no modelo **não implicam causalidade**.

## LGPD Compliance

- Input data: TSE público + IBGE público — sem dados individuais
- Nível mínimo de agregação: município
- Armazenamento: GCS southamerica-east1 + BigQuery southamerica-east1
- Retenção: dados TSE históricos indefinidamente (registro público); sinal digital: 1 ano

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-18 | Initial model card — MVP bootstrap |
