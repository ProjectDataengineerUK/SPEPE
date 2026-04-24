# Metodologia de Modelagem Bayesiana

## Abordagem SPEPE

### MVP — Bootstrap Logístico
- Regressão logística multinomial com bootstrap (n=1000 amostras)
- IC 95% via percentis 2.5% e 97.5% da distribuição bootstrap
- Features: top-15 SHAP do ciclo anterior ou default set
- Código: `mlops/components/train_bootstrap.py`

### Produção — PyMC HLM (Hierarchical Linear Model)
- Modelo hierárquico: município → UF → Brasil
- Prior informativo: resultados eleitorais históricos (2014, 2018)
- Likelihood: Dirichlet-Multinomial (composição de votos)
- Código: `mlops/pymc_model.py`

## Agregação de Pesquisas (Polling Average)

- House effect ajustado: cada instituto tem viés histórico estimado
- Peso por tamanho amostral e recência (decay exponencial, half-life = 14 dias)
- Código: `mlops/poll_aggregator.py`

## Features Padrão (default_features)

```python
DEFAULT_FEATURES = [
    "renda_media_domiciliar", "pct_analfabetos", "taxa_desemprego",
    "pct_rural", "idhm", "pct_evangelicos", "populacao",
    "pct_votos_candidato_ant",  # votação anterior do candidato no mesmo cargo
    "pib_per_capita", "gini",
    "sg_regiao",                # Norte/Nordeste/Centro-Oeste/Sudeste/Sul
]
```

## Outputs Esperados

```python
{
    "candidato": "Lula",
    "eleicao": "Presidente 2026",
    "p_vence_1t": 0.31,        # P(vence no 1º turno)
    "p_vence_2t": 0.58,        # P(vence no 2º turno)
    "ci_lower_1t": 0.24,
    "ci_upper_1t": 0.39,
    "ci_lower_2t": 0.51,
    "ci_upper_2t": 0.65,
    "n_bootstrap": 1000,
    "shap_top10": [...]         # para o Explicador
}
```

## Disclaimers Obrigatórios

1. Baseado em dados históricos — não captura eventos futuros
2. Incerteza aumenta quanto mais longe da data da eleição
3. Modelo treinado em {UF}/{ano} pode ter bias em outras UFs
4. Não substitui análise eleitoral profissional
5. Para fins analíticos e educacionais

## Limites do Modelo

- Menos de 100 seções na amostra → IC muito largo → avisar usuário
- Candidatos sem histórico eleitoral → prior flat → incerteza alta
- Eleições municipais têm dinâmica diferente de eleições estaduais/federais
