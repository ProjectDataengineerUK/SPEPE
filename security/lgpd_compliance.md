# SPEPE — LGPD Compliance Documentation

## Visão Geral

O SPEPE foi desenvolvido com LGPD (Lei Geral de Proteção de Dados — Lei 13.709/2018) em mente desde a concepção.

## Dados Processados

### TSE (Tribunal Superior Eleitoral)
- **Natureza**: Dados públicos de resultado eleitoral
- **Granularidade**: Seção eleitoral (nunca indivíduo)
- **Identificador mínimo**: Município × Zona × Seção × Candidato
- **Base legal**: Art. 7º, II (execução de contrato / pesquisa pública)

### IBGE
- **Natureza**: Dados públicos censitários e estatísticos
- **Granularidade**: Município e setor censitário (nunca domicílio individual)
- **Base legal**: Art. 7º, II e IX (pesquisa)

### Sinal Digital (Meta, Google Trends, YouTube)
- **Política**: Nunca armazenamos dados a nível de usuário individual
- **Granularidade mínima**: Município para Meta Ads, Estado para Trends
- **Anonimização**: Agregação antes de qualquer persistência
- **Base legal**: Art. 11, §2º (dados não-identificáveis)

## Medidas Técnicas

| Medida | Implementação |
|--------|---------------|
| Armazenamento em solo brasileiro | GCS + BigQuery southamerica-east1 |
| Controle de acesso | IAP + IAM least-privilege |
| Criptografia em repouso | GCS default encryption (Google-managed) |
| Criptografia em trânsito | HTTPS/TLS 1.3 via Cloud Run |
| Auditoria | Cloud Logging + Cloud Audit Logs |
| DLP (Data Loss Prevention) | Hook `dlp_hook.py` em todos os outputs de agentes |
| Minimização de dados | Gold tables sem identificadores individuais |

## Restrições Implementadas No Código

1. **`hooks/dlp_hook.py`**: Bloqueia qualquer output de agente contendo CPF, e-mail individual, ou nome + data de nascimento
2. **Nível mínimo de agregação**: Constante `LGPD_MIN_AGGREGATE_LEVEL=municipio` — dados digitais sempre agregados ao município antes de qualquer output
3. **Bronze imutável**: Dados brutos nunca sobrepostos — auditabilidade garantida
4. **Secret Manager**: Credenciais de API nunca em código ou variáveis de ambiente de produção

## Encarregado de Dados (DPO)

Para este projeto acadêmico/pessoal, o responsável é o próprio pesquisador.
Em caso de produto comercial, nomear DPO conforme Art. 41 LGPD.

## Direitos dos Titulares

Os dados processados pelo SPEPE são **públicos e não identificam indivíduos**.
Não há titulares de dados pessoais identificáveis no sistema.

## Incidente de Dados

Em caso de incidente (vazamento de credenciais, acesso não autorizado):
1. Revogar service accounts imediatamente via `gcloud iam service-accounts disable`
2. Rotacionar secrets no Secret Manager
3. Notificar ANPD se houver dados pessoais afetados (não aplicável para dados públicos agregados)

## Revisão

Este documento deve ser revisado a cada 12 meses ou em caso de mudança de escopo.
Última revisão: 2026-04-18
