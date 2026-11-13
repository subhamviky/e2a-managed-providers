#!/usr/bin/env bash
# Run each project's OWN test suite with its model binding swapped to every provider
# profile (offline: real provider SDK, replayed wire; the projects' rules supply content).
#   PROJECTS_DIR=../ ./examples/run_project_suites.sh [profile ...]
# E2A_SWAP_LIVE=true sends the calls to the providers for real (credentials required).
set -euo pipefail
PROJECTS_DIR="${PROJECTS_DIR:-..}"
PROFILES=("$@")
[ ${#PROFILES[@]} -eq 0 ] && PROFILES=($(python -m e2a_managed profiles | awk '{print $1}'))
SUITES=(
  "order-to-cash-agentic-ai/o2c-agent-service|o2c_agent_service.services.llm:build_o2c_llm"
  "aws-reconciliation-engine/reconciliation-service|reconciliation_service.services.llm:build_triage_llm"
  "financial-settlement-platform/audit-ai-service|audit_ai_service.services.llm:build_audit_llm"
)
status=0
for profile in "${PROFILES[@]}"; do
  for suite in "${SUITES[@]}"; do
    dir="${PROJECTS_DIR}/${suite%%|*}"; factory="${suite##*|}"
    [ -d "$dir" ] || { echo "skip $dir (not checked out)"; continue; }
    result=$(cd "$dir" && E2A_PROVIDER_PROFILE="$profile" E2A_SWAP_FACTORIES="$factory" \
             python -m pytest -q -p e2a_managed.pytest_plugin -p no:cacheprovider 2>&1 | tail -1) || status=1
    printf '%-32s %-50s %s\n' "$profile" "${suite%%|*}" "$result"
  done
done
exit $status
