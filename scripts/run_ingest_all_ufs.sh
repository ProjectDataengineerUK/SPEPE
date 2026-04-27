#!/usr/bin/env bash
# Ingestão TSE + IBGE para todas as 27 UFs — executa Cloud Run Jobs sequencialmente.
set -euo pipefail

PROJECT="spepe-dev"
REGION="southamerica-east1"
YEAR="2022"
IMAGE="southamerica-east1-docker.pkg.dev/spepe-dev/spepe/app:b538aa0f"

UFS=(AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO)

echo "=== Atualizando imagem dos jobs ==="
for job in spepe-tse-ingest spepe-ibge-sync spepe-silver-transform spepe-gold-build; do
  gcloud run jobs update "$job" \
    --image "$IMAGE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --quiet
  echo "  ✓ $job atualizado"
done

echo ""
echo "=== Iniciando ingestão: ${#UFS[@]} UFs, ano=$YEAR ==="
FAILED=()

for UF in "${UFS[@]}"; do
  echo ""
  echo "--- [$UF] TSE ingest ---"
  if gcloud run jobs execute spepe-tse-ingest \
      --update-env-vars "DEFAULT_UF=${UF},DEFAULT_ANO=${YEAR}" \
      --region "$REGION" \
      --project "$PROJECT" \
      --wait \
      --quiet 2>&1; then
    echo "  ✓ TSE $UF concluído"
  else
    echo "  ✗ TSE $UF FALHOU"
    FAILED+=("TSE-$UF")
  fi

  echo "--- [$UF] IBGE sync ---"
  if gcloud run jobs execute spepe-ibge-sync \
      --update-env-vars "DEFAULT_UF=${UF}" \
      --region "$REGION" \
      --project "$PROJECT" \
      --wait \
      --quiet 2>&1; then
    echo "  ✓ IBGE $UF concluído"
  else
    echo "  ✗ IBGE $UF FALHOU"
    FAILED+=("IBGE-$UF")
  fi
done

echo ""
echo "=== Resumo ==="
echo "UFs processadas: ${#UFS[@]}"
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "Todos os jobs concluídos com sucesso."
else
  echo "Falhas (${#FAILED[@]}): ${FAILED[*]}"
  exit 1
fi
