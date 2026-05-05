#!/usr/bin/env bash
# Ingestão completa Fase 1 — todas as fontes → Silver → Gold → semantic views.
# Executa Cloud Run Jobs sequencialmente; falhas por UF não abordam o pipeline.
#
# Uso:
#   ./scripts/run_ingest_all_ufs.sh              # todas as 27 UFs, ano 2022
#   UFS="SP RJ MG" YEAR=2022 ./scripts/run_ingest_all_ufs.sh   # subconjunto
#   SKIP_NATIONAL=1 ./scripts/run_ingest_all_ufs.sh             # só per-UF
#   SKIP_PER_UF=1 ./scripts/run_ingest_all_ufs.sh               # só nacionais
set -euo pipefail

PROJECT="${PROJECT:-spepe-dev}"
REGION="${REGION:-southamerica-east1}"
YEAR="${YEAR:-2022}"
UFS="${UFS:-AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO}"

SKIP_NATIONAL="${SKIP_NATIONAL:-0}"
SKIP_PER_UF="${SKIP_PER_UF:-0}"
SKIP_OPTIONAL="${SKIP_OPTIONAL:-1}"   # social/digital/reddit — exigem API keys
DRY_RUN="${DRY_RUN:-0}"

# ── helpers ───────────────────────────────────────────────────────────────────

FAILED=()
SKIPPED=()

run_job() {
  local job_name="$1"; shift
  local label="$1"; shift
  local extra_args=("$@")

  printf '\n--- %s (%s) ---\n' "$label" "$job_name"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] gcloud run jobs execute $job_name ${extra_args[*]:-}"
    return 0
  fi

  local cmd=(
    gcloud run jobs execute "$job_name"
    --region "$REGION"
    --project "$PROJECT"
    --wait
    --quiet
  )
  if [[ ${#extra_args[@]} -gt 0 ]]; then
    # Build comma-separated string, skipping empty elements
    local args_csv=""
    for arg in "${extra_args[@]}"; do
      [[ -n "$arg" ]] && args_csv+="${args_csv:+,}${arg}"
    done
    # Use --args=VALUE (not --args VALUE) so gcloud doesn't misparse values starting with --
    [[ -n "$args_csv" ]] && cmd+=("--args=${args_csv}")
  fi

  if "${cmd[@]}" 2>&1; then
    echo "  ✓ $label concluído"
  else
    echo "  ✗ $label FALHOU"
    FAILED+=("$label")
  fi
}

run_job_uf() {
  local job_name="$1"
  local label_prefix="$2"
  local uf="$3"
  shift 3
  local extra_args=("$@")

  run_job "$job_name" "${label_prefix} [${uf}]" "--uf" "$uf" "${extra_args[@]+"${extra_args[@]}"}"
}

# ── fase 1 — jobs nacionais ───────────────────────────────────────────────────

if [[ "$SKIP_NATIONAL" != "1" ]]; then
  echo "======================================================================"
  echo "FASE 1 — Jobs nacionais (sem loop de UF)"
  echo "======================================================================"

  run_job "spepe-pesquisas-ingest" "Pesquisas eleitorais (TSE PesqEle + Atlas)"
  run_job "spepe-camara-senado-ingest" "Câmara + Senado (votações, CEAP, parlamentares)"
  run_job "spepe-tse-candidaturas-ingest" "TSE Candidaturas (BR)"
fi

# ── fase 2 — jobs por UF ─────────────────────────────────────────────────────

if [[ "$SKIP_PER_UF" != "1" ]]; then
  echo ""
  echo "======================================================================"
  printf 'FASE 2 — Jobs por UF (%s)\n' "$UFS"
  echo "======================================================================"

  read -ra UF_ARRAY <<< "$UFS"
  echo "UFs a processar: ${#UF_ARRAY[@]}"

  for UF in "${UF_ARRAY[@]}"; do
    echo ""
    echo "══ UF: $UF ══════════════════════════════════════════════════════════"

    # TSE resultados eleitorais
    run_job_uf "spepe-tse-ingest" "TSE resultados" "$UF" "--year" "$YEAR"

    # TSE perfis de candidatos
    run_job_uf "spepe-tse-perfil-ingest" "TSE perfis candidatos" "$UF"

    # IBGE — demographics + socioeconomic indicators
    run_job_uf "spepe-ibge-sync" "IBGE sync" "$UF"

    # Segurança pública (IVS, Atlas da Violência, SINESP)
    run_job_uf "spepe-security-ingest" "Segurança pública" "$UF"

    # Saúde pública (DATASUS — mortalidade, cobertura ANS)
    run_job_uf "spepe-datasus-ingest" "DataSUS saúde" "$UF"

    # Economia (DIEESE cesta básica)
    run_job_uf "spepe-dieese-ingest" "DIEESE cesta básica" "$UF"

    # Acesso digital (CETIC TIC Domicílios)
    run_job_uf "spepe-cetic-ingest" "CETIC acesso digital" "$UF"
  done
fi

# ── fase 3 — opcional (exige API keys) ───────────────────────────────────────

if [[ "$SKIP_OPTIONAL" != "1" ]]; then
  echo ""
  echo "======================================================================"
  echo "FASE 3 — Jobs opcionais (requerem API keys em Secret Manager)"
  echo "======================================================================"

  run_job "spepe-digital-ingest" "Google Trends + Meta Ads"
  run_job "spepe-social-ingest"  "Social listening (Twitter/X, Facebook)"
  run_job "spepe-reddit-ingest"  "Reddit brasil/politica/brasilivre"
fi

# ── fase 4 — transformação silver → gold ─────────────────────────────────────

echo ""
echo "======================================================================"
echo "FASE 4 — Transformação Silver → Gold (USE_BIGQUERY=true)"
echo "======================================================================"

run_job "spepe-silver-transform" "Silver transform (Bronze → Silver BQ)" \
    "--uf" "ALL"

run_job "spepe-gold-build" "Gold build (Silver → Gold BQ)"

# ── fase 5 — semantic layer ───────────────────────────────────────────────────

echo ""
echo "======================================================================"
echo "FASE 5 — Criação das views semânticas no BigQuery Gold"
echo "======================================================================"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "  [dry-run] python -m dataops.semantic_layer"
else
  gcloud run jobs execute spepe-gold-build \
    --args "python,-m,dataops.semantic_layer" \
    --region "$REGION" \
    --project "$PROJECT" \
    --wait \
    --quiet 2>/dev/null \
  || python -m dataops.semantic_layer 2>&1 \
  && echo "  ✓ Semantic views criadas" \
  || { echo "  ⚠ Semantic layer: rode manualmente: python -m dataops.semantic_layer"; SKIPPED+=("semantic_layer"); }
fi

# ── resumo ────────────────────────────────────────────────────────────────────

echo ""
echo "======================================================================"
echo "RESUMO"
echo "======================================================================"

if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "✅ Todos os jobs concluídos com sucesso."
else
  echo "❌ Falhas (${#FAILED[@]}): ${FAILED[*]}"
fi

if [[ ${#SKIPPED[@]} -gt 0 ]]; then
  echo "⚠  Ignorados: ${SKIPPED[*]}"
fi

echo ""
echo "Próximo passo — ativar BigQuery no serviço principal:"
echo "  gcloud run services update spepe-dev \\"
echo "    --update-env-vars USE_BIGQUERY=true \\"
echo "    --region $REGION --project $PROJECT"

[[ ${#FAILED[@]} -eq 0 ]] || exit 1
