#!/usr/bin/env bash
# Run each project's OWN test suite with its model binding swapped to every provider
# profile (offline: real provider SDK, replayed wire; the projects' rules supply content).
#
#   PROJECTS_DIR=../ ./examples/run_project_suites.sh [profile ...]
#
# ISOLATED=1 builds one virtualenv per project (in $VENVS_DIR) from the project's own
# pyproject (its own dependencies and pins), then adds this package with its dependencies:
# the project then runs on the E2A release this package needs (>= v1.6.0, for e2a.bindings),
# which also proves the project's own suite passes on it.
# E2A_SWAP_LIVE=true sends the calls to the providers for real (credentials required).
#
# With FIRESTORE_EMULATOR_HOST / COSMOS_EMULATOR_ENDPOINT set, O2C and audit-ai-service run a
# second time with their state on the emulator (their multi-instance / multi-replica tests).
# EVIDENCE_FILE=<path> writes the results as a Markdown table (CI adds it to the run summary).
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
              "llama-stack-client>=0.7" boto3 azure-identity azure-cosmos google-cloud-firestore
              pytest pytest-asyncio "moto[dynamodb,sqs]>=5.0" jsonschema)
# The Cosmos DB emulator's published, well-known key (not a secret).
COSMOS_EMULATOR_KEY="C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="

state_env() {                       # $1 = project dir; prints the env of its stateful pass, if any
  case "$1" in
    *o2c-agent-service) [ -n "${FIRESTORE_EMULATOR_HOST:-}" ] && \
        echo "O2C_STATE_BACKEND=firestore GOOGLE_CLOUD_PROJECT=demo-o2c";;
    *audit-ai-service) [ -n "${COSMOS_EMULATOR_ENDPOINT:-}" ] && \
        echo "AUDIT_STATE_BACKEND=cosmos COSMOS_ENDPOINT=$COSMOS_EMULATOR_ENDPOINT COSMOS_KEY=$COSMOS_EMULATOR_KEY";;
  esac
  return 0
}

python_for() {                      # $1 = project dir; prints the python to use
  if [ "${ISOLATED:-0}" != "1" ]; then command -v python; return; fi
  local venv="$VENVS_DIR/$(basename "$1")"
  if [ ! -x "$venv/bin/python" ]; then
    python -m venv "$venv" >&2
    "$venv/bin/pip" install -q --upgrade pip >&2
    "$venv/bin/pip" install -q -e "$1" >&2                       # the project with its own pins
    "$venv/bin/pip" install -q "${RUNTIME_DEPS[@]}" >&2
    "$venv/bin/pip" install -q -e "$HERE" >&2                    # this package with its E2A (>= v1.6.0)
  fi
  echo "$venv/bin/python"
}

status=0
[ -n "${EVIDENCE_FILE:-}" ] && printf '| Profile | Project | State | Result |\n|---|---|---|---|\n' > "$EVIDENCE_FILE"
run_one() {                         # $1 dir  $2 factory  $3 profile  $4 label  $5 extra env
  local out rc summary
  out=$(cd "$1" && env $5 E2A_PROVIDER_PROFILE="$3" E2A_SWAP_FACTORIES="$2" \
        "$py" -m pytest -q -rs -p e2a_managed.pytest_plugin -p no:cacheprovider 2>&1) && rc=0 || rc=$?
  summary=$(echo "$out" | grep -E "passed|failed|error" | tail -1)
  printf '%-32s %-50s %-10s %s\n' "$3" "${1#$PROJECTS_DIR/}" "$4" "$summary"
  [ -n "${EVIDENCE_FILE:-}" ] && printf '| %s | %s | %s | %s |\n' "$3" "${1#$PROJECTS_DIR/}" "$4" "$summary" >> "$EVIDENCE_FILE"
  if [ $rc -ne 0 ]; then status=1; echo "$out" | grep -E "^(E |FAILED|ERROR)" | head -15; fi
}
for suite in "${SUITES[@]}"; do
  dir="$PROJECTS_DIR/${suite%%|*}"; factory="${suite##*|}"
  [ -d "$dir" ] || { echo "skip $dir (not checked out)"; continue; }
  py="$(python_for "$dir")"
  extra="$(state_env "$dir")"
  for profile in "${PROFILES[@]}"; do
    run_one "$dir" "$factory" "$profile" "in memory" ""
    [ -n "$extra" ] && run_one "$dir" "$factory" "$profile" "emulator" "$extra"
  done
done
exit $status
