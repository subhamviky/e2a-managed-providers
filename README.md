# e2a-managed-providers

Frontier-provider managed services (Anthropic, OpenAI, Google, Meta) behind the
vendor-neutral E2A contracts, chosen by configuration.

The three projects of the Order-to-Cash landscape
([order-to-cash-agentic-ai](https://github.com/subhamviky/order-to-cash-agentic-ai),
[aws-reconciliation-engine](https://github.com/subhamviky/aws-reconciliation-engine),
[financial-settlement-platform](https://github.com/subhamviky/financial-settlement-platform))
are built on [E2A](https://github.com/subhamviky/e2a-framework) and run on
`SimulatedLLMService`, with cloud as Terraform and containers as Compose. This repository
answers one question: **can those E2A seams carry real managed AI services without
changing project code?** It does this in code, with no provider or cloud account, in the
same way the projects treat cloud (IaC tested against mock providers).

> This is an architecture reference, like the projects. Nothing here has been deployed
> or called against a live provider. What *is* executed is listed under
> [Evidence](#evidence); what is not is in [docs/CHALLENGES.md](docs/CHALLENGES.md).

## The profiles

A profile is one YAML file: it binds a class to each E2A seam and supplies the config
layer of E2A's resolution order (`kwargs > config > env > default`).

| Profile | Models | Retrieval | Guardrails | Agent runtime | State / telemetry | Cloud IaC |
|---|---|---|---|---|---|---|
| `anthropic_bedrock` | Claude on Amazon Bedrock | Titan embeddings, S3 Vectors, Bedrock Rerank (Cohere) | Bedrock Guardrails | AgentCore Runtime + Gateway (MCP) | DynamoDB, CloudWatch/X-Ray | AWS |
| `anthropic_claude_platform_aws` | Claude Platform on AWS (MCP connector, server-side fallbacks) | same AWS services | Bedrock Guardrails | Claude Managed Agents (`ant apply`) | DynamoDB, CloudWatch/X-Ray | AWS |
| `openai_azure` | OpenAI on Azure AI Foundry (v1 Responses API, Entra ID) | text-embedding-3, Azure AI Search (hybrid + semantic ranker) | AI Content Safety (Prompt Shields) | Responses-API agent, APIM as MCP gateway | Cosmos DB, App Insights | Azure |
| `google_vertex` | Gemini on Vertex AI | gemini-embedding, Vector Search (dense+sparse), Ranking API | Model Armor | Vertex AI Agent Engine, IAP MCP gateway | Firestore, Cloud Trace | GCP |
| `meta_llama_aws` | Llama via a Llama Stack distribution (Bedrock inference) on ECS | Llama Stack embeddings + vector_io | Llama Guard shields | Llama Stack Responses API | DynamoDB, CloudWatch | AWS |

Each pairing is the one most commonly used in practice: Claude mostly runs on Bedrock (and
on Claude Platform on AWS for Anthropic's full API surface), OpenAI on Azure, and Gemini on
Vertex. Meta has no cloud of its own, so its enterprise path is Llama Stack hosted on a
cloud; AWS is used here. Cohere appears where enterprises actually meet it, as the Bedrock
Rerank model. A fifth vendor was left out on purpose (see docs/CHALLENGES.md).

`python -m e2a_managed matrix` prints the [capability matrix](docs/CAPABILITY_MATRIX.md),
generated from the bindings.

## Switching provider is configuration

```python
from e2a_managed import bindings_from_profile

bindings = bindings_from_profile()                 # E2A_PROVIDER_PROFILE=openai_azure
service = AssistantService(..., llm=bindings.llm_service)
workflow.execute(state, config=bindings.config(tools=tools, governance_engine=governance))
```

Today each project builds `SimulatedLLMService` in one factory (`build_o2c_llm()`,
`build_triage_llm()`, `build_audit_llm()`). That factory is the one line a project changes
to adopt a profile. After that, a provider change is `E2A_PROVIDER_PROFILE` plus that
provider's variables (`python -m e2a_managed env <profile>` prints them).

**Vendor parameters per call.** Every vendor key is registered in E2A's central registry,
so the framework's own rule applies: a keyword passed to the inherited public method wins
for that call.

```python
agent.run(state, config, reasoning_effort="high")      # Claude effort / OpenAI reasoning / Gemini thinking
llm.process_inference(messages, tenant_id="acme", model_id="anthropic.claude-opus-4-8")
rag.retrieve(query, config, tenant_id="acme", rerank_top_n=5)
```

Java follows the same rule: `ctx.withSettings(Map.of("reasoning_effort", "high"))` wins
over the environment ([java/](java/)).

## Layout

```
e2a_managed/                     provider-neutral core (an upstream candidate for E2A)
  config_keys.py  profile.py  bindings.py  capabilities.py      configuration → bound seams
  codec.py                       canonical conversation ↔ Anthropic / OpenAI Responses / OpenAI Chat / Gemini wire
  managed_llm.py                 ManagedLLMService(BaseLLMAgentService): provider hooks, features, refusals
  managed_rag.py                 embedding / index bases, ManagedReranker(e2a.BaseReranker), ManagedRAGPipeline
  guardrails.py                  E2A's ManagedGuardrailStage (v1.6.1), re-exported; providers add _screen
  delegated.py                   E2A's DelegatedRuntime (v1.6.1), re-exported; bound per agent with a scope
  mcp_gateway.py                 ManagedMCPGatewayTool(MCPToolService)
  observability.py  iac.py  release.py                          BaseObservability / BaseInfraProvisioner / BasePipeline
  offline.py  swap.py  pytest_plugin.py                         offline wire replay through the real SDKs
  providers/
    anthropic_aws/   llm_service.py  delegated_runtime.py  managed_agents/  profiles/
    openai_azure/    llm_service.py  delegated_runtime.py  profiles/
    google_vertex/   llm_service.py  profiles/
    meta_llama/      llm_service.py  infra.py  distribution/run.yaml  terraform/  profiles/
  clouds/                        services shared by every provider on a cloud
    aws/    bedrock.py  substrate.py  terraform/ (+ extensions/)
    azure/  services.py  terraform/
    gcp/    services.py  terraform/ (+ extensions/)
java/                            LlmService adapters for e2a-core (Java) on the official SDKs
examples/                        run_landscape.py, run_project_suites.sh, live_smoke.py
.github/workflows/               ci.yml (no accounts), deploy-{aws,azure,gcp}.yml (OIDC, inert by default)
docs/                            ARCHITECTURE, CAPABILITY_MATRIX, TESTING, CHALLENGES, UPSTREAM
```

## Evidence

Everything below runs with no account (in CI on every push):

| Check | Result |
|---|---|
| This package's tests: profiles, every LLM adapter through its **real SDK** (unary, streaming, tool round trip, structured output, per-call override, refusal and fallback, E2A DLP still applied), AWS services against AWS's own API models (botocore Stubber), Azure/GCP/Llama Stack REST shapes, **E2A's model contract (`check_llm_service`, 10 scenarios) on every profile**, the claim and checkpoint stores (e2a-adapters) against **E2A's own contract kit** (`e2a.port_contract`) on moto and, races included, on DynamoDB Local and the Firestore and Cosmos DB emulators, delegated runtimes, IaC manifests, release gate, E2A conformance | 93 passed (90 without the emulators) |
| The three projects' **own** test suites, at their latest `main`, each in its own venv on the E2A release it pins (v1.4.0 or v1.5.0), with the model binding swapped to each of the 5 profiles; O2C and audit-ai a second time with their state on the Firestore and Cosmos DB emulators | O2C 43 + 43, reconciliation 44, audit-ai 23 + 26, on every profile; no project file changed. CI writes the full table to each run's summary (`EVIDENCE_FILE`). |
| Landscape scenario per profile (`examples/run_landscape.py`): policy answer, payment exceptions, order placed, order paused for four-eyes approval, audit finding flagged and explained | all 5 profiles |
| Java adapters (anthropic-java, openai-java, google-genai) against local wire replay | 5 passed |
| Terraform `fmt`, `validate` and `test` with mock providers, every module | AWS 3, GCP 2, Azure 2 tests pass (locally and in CI) |

### Supported E2A versions

This package needs **E2A v1.7.0 or later** (it pins the `v1.7.0` tag). **The cloud infrastructure is
e2a-adapters' (1.7.0):** every provider profile `extends` the cloud profile of its cloud (`aws`, `gcp`,
`azure`), which binds the stores, the message broker, the secret store and the identity claims; a
provider profile adds only the AI services and overrides what it must. Its profiles, bindings
and capabilities are E2A's own (`e2a.bindings`, `e2a.capabilities`), its refusals are E2A's
`ModelRefusal`, and every adapter is checked with E2A's model contract
(`e2a.port_contract.check_llm_service`). Since v1.6.1 the vendor-neutral half of every managed
service is E2A's too, and this package keeps only the vendor half:

| E2A (neutral) | Here (vendor) |
|---|---|
| `model_routes`; `refusal_fallback_server` skips E2A's route | `fallback_model_id` becomes a refusal route; Claude Platform on AWS falls back server-side |
| the `features` map: E2A drops or refuses what a model cannot take, and checks every structured result | each adapter's `features` and its `reasoning_effort` mapping |
| `ManagedGuardrailStage` and its egress variant: fail closed, `FirewallRefusal` | Bedrock Guardrails, Prompt Shields, Model Armor, Llama Guard (`_screen`) |
| `DelegatedRuntime`: read tools only, tenant-scoped tool calls, `delegated_max_turns` | Managed Agents, Responses API, AgentCore, Agent Engine sessions |
| `BaseReranker` port, `dimensions` check | Bedrock Rerank, Vertex Ranking, Azure semantic ranker |
| `_auth_headers` hook | gateway identity (AgentCore, APIM, IAP) |
| `scopes` | profiles that bind a runtime to one agent: `scopes: {"agent:<name>": {"llm_service": "@delegated_runtime"}}` | It is registered as an E2A provider package (entry
point `e2a.providers`), which is what lets profiles bind its classes.

The project suites run each project on that E2A release (the runner installs this package
with its dependencies into each project's venv), which also proves the projects' own suites
pass on it.

### Retries

E2A (v1.5.1+) retries a model call by error class: throttled, overloaded and transient
failures, honouring `Retry-After` within the request deadline, and throttling never opens the
circuit. So the provider SDKs do not retry (`llm_sdk_max_retries` = 0): one retry authority.
`check_llm_service` proves it for every profile: a replayed HTTP 429 is retried by E2A through
each provider's real SDK.

Wire replay means the real provider SDK builds, signs (SigV4 for Bedrock) and parses the
calls; only the HTTP endpoint is replaced by a responder backed by the projects' own rules.
See [docs/TESTING.md](docs/TESTING.md).

## Quick start

```bash
pip install -e ".[dev]"
pytest -q
python -m e2a_managed profiles
python -m e2a_managed verify openai_azure
python examples/run_landscape.py google_vertex          # needs the three projects installed
```
