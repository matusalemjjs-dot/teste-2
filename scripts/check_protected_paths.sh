#!/usr/bin/env bash
# Falha se algum caminho protegido (algoritmos de seleção, transcrição, renderização e validação de mídia)
# foi alterado em relação a uma referência. Mudanças nesses caminhos exigem aprovação explícita.
# Uso: scripts/check_protected_paths.sh <commit-ou-branch-de-referência>   (padrão: origin/main)
set -euo pipefail
BASE="${1:-origin/main}"
PROTECTED=(
  backend/app/analysis
  backend/app/transcription
  backend/app/rendering
  backend/app/services/media_probe.py
)
CHANGED=$(git diff --name-only "$BASE" -- "${PROTECTED[@]}"; git diff --name-only -- "${PROTECTED[@]}"; \
          git ls-files --others --exclude-standard -- "${PROTECTED[@]}")
if [ -n "$CHANGED" ]; then
  echo "ERRO: caminhos protegidos alterados desde $BASE (exige aprovação):"
  echo "$CHANGED" | sort -u
  exit 1
fi
echo "OK: nenhum caminho protegido alterado desde $BASE."
