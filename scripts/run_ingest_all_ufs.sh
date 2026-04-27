#!/usr/bin/env bash
# Ingestão TSE + IBGE para todas as 27 UFs — executa Cloud Run Jobs sequencialmente.
set -euo pipefail

PROJECT="spepe-dev"
REGION="southamerica-east1"
YEAR="2022"
IMAGE="southamerica-east1-docker.pkg.dev/spepe-dev/spepe/app:b538aa0f"

UFS=(AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO)

echo "=== Atualizando imagem e memória dos jobs ==="
for job in spepe-ibge-sync spepe-silver-transform spepe-gold-build; do
  gcloud run jobs update "$job" \
    --image "$IMAGE" \
    --memory 2Gi --cpu 1 \
    --region "$REGION" \
    --project "$PROJECT" \
    --quiet
  echo "  ✓ $job atualizado"
done
# TSE precisa de mais memória — zips grandes
gcloud run jobs update spepe-tse-ingest \
  --image "$IMAGE" \
  --memory 4Gi --cpu 2 \
  --region "$REGION" \
  --project "$PROJECT" \
  --quiet
echo "  ✓ spepe-tse-ingest atualizado (4Gi)"

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
echo "=== Transformação Silver → Gold ==="
echo "--- Silver transform ---"
if gcloud run jobs execute spepe-silver-transform \
    --update-env-vars "USE_BIGQUERY=true" \
    --region "$REGION" --project "$PROJECT" --wait --quiet 2>&1; then
  echo "  ✓ Silver transform concluído"
else
  echo "  ✗ Silver transform FALHOU"
  FAILED+=("SILVER")
fi

echo "--- Gold build ---"
if gcloud run jobs execute spepe-gold-build \
    --update-env-vars "USE_BIGQUERY=true" \
    --region "$REGION" --project "$PROJECT" --wait --quiet 2>&1; then
  echo "  ✓ Gold build concluído"
else
  echo "  ✗ Gold build FALHOU"
  FAILED+=("GOLD")
fi

echo ""
echo "=== Resumo ==="
echo "UFs processadas: ${#UFS[@]}"
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "Todos os jobs concluídos com sucesso."
  echo ""
  echo "Próximo passo: setar USE_BIGQUERY=true no serviço principal:"
  echo "  gcloud run services update spepe-dev --update-env-vars USE_BIGQUERY=true --region $REGION --project $PROJECT"
else
  echo "Falhas (${#FAILED[@]}): ${FAILED[*]}"
  exit 1
fi
