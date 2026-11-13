#!/usr/bin/env bash
# Run each project's OWN test suite with its model binding swapped to every provider
# profile (offline: real provider SDK, replayed wire; the projects' rules supply content).
#
#   PROJECTS_DIR=../ ./examples/run_project_suites.sh [profile ...]
#
# ISOLATED=1 builds one virtualenv per project (in $VENVS_DIR) from the project's own
# pyproject, because the projects pin different E2A releases (e.g. v1.4.0 and v1.5.0)
# that cannot share one environment. This package is added to each venv without its
# dependencies, so it runs on the E2A version the project pins.
# E2A_SWAP_LIVE=true sends the calls to the providers for real (credentials required).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROJECTS_DIR="$(cd "${PROJECTS_DIR:-..}" && pwd)"
VENVS_DIR="${VENVS_DIR:-$HERE/.venvs}"
PROFILES=("$@")
[ ${#PROFILES[@]} -eq 0 ] && PROFILES=($(cd "$HERE" && python -m e2a_managed profiles | awk '{print $1}'))
SUITES=(
  "order-to-cash-agentic-ai/o2c-agent-service|o2c_agent_service.services.llm:build_o2c_llm"
  "aws-reconciliation-engine/reconciliation-service|reconciliation_service.services.llm:build_triage_llm"
  "financial-settlement-platform/audit-ai-service|audit_ai_service.services.llm:build_audit_llm"
)
# The SDKs every profile's model adapter needs, plus the test tooling the projects use.
RUNTIME_DEPS=(pyyaml "httpx>=0.27" "anthropic[bedrock,aws]>=2.0" "openai>=3.0" "google-genai>=3.0"
              "llama-stack-client>=0.7" boto3 azure-identity pytest pytest-asyncio
              "moto[dynamodb,sqs]>=5.0" jsonschema)

python_for() {                      # $1 = project dir; prints the python to use
  if [ "${ISOLATED:-0}" != "1" ]; then command -v python; return; fi
  local venv="$VENVS_DIR/$(basename "$1")"
  if [ ! -x "$venv/bin/python" ]; then
    python -m venv "$venv" >&2
    "$venv/bin/pip" install -q --upgrade pip >&2
    "$venv/bin/pip" install -q -e "$1" >&2                       # the project with its own pins
    "$venv/bin/pip" install -q "${RUNTIME_DEPS[@]}" >&2
    "$venv/bin/pip" install -q -e "$HERE" --no-deps >&2          # this package, on the project's E2A
  fi
  echo "$venv/bin/python"
}

status=0
for suite in "${SUITES[@]}"; do
  dir="$PROJECTS_DIR/${suite%%|*}"; factory="${suite##*|}"
  [ -d "$dir" ] || { echo "skip $dir (not checked out)"; continue; }
  py="$(python_for "$dir")"
  for profile in "${PROFILES[@]}"; do
    out=$(cd "$dir" && E2A_PROVIDER_PROFILE="$profile" E2A_SWAP_FACTORIES="$factory" \
          "$py" -m pytest -q -p e2a_managed.pytest_plugin -p no:cacheprovider 2>&1) && rc=0 || rc=$?
    summary=$(echo "$out" | grep -E "passed|failed|error" | tail -1)
    printf '%-32s %-50s %s\n' "$profile" "${suite%%|*}" "$summary"
    if [ $rc -ne 0 ]; then status=1; echo "$out" | grep -E "^(E |FAILED|ERROR)" | head -15; fi
  done
done
exit $status
