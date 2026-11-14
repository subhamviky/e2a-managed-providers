# E2A Improvement Proposal — v2

| | |
|---|---|
| Supersedes | `E2A_V1_4_IMPROVEMENT_PROPOSAL.md` (v1). v1 stays as the detailed rationale; this document decides. |
| Baseline | E2A v1.5.0 (`e2a.port_contract`, `e2a-adapters`: DynamoDB, Firestore, Cosmos DB on one CAS engine). `e2a-managed-providers` at `c32ebde`. |
| Target | The next E2A releases (v1.5.1 onwards). v1 said "v1.4", but that number is now taken by the adapters release. |
| Scope | Only what can be built and verified **without any cloud or provider account**. |

## 1. The rule this version applies

**E2A extends managed services. It never replaces them.** A vendor's managed service does the
work: model, search, guardrail, broker, store, workflow engine. E2A adds what the vendor does
not provide: enterprise rigor across vendors.

* typed outcomes and exactly-once semantics (claims, outbox, reconciliation);
* tenant isolation and propagation (request context, scopes);
* governance order (screen → decide → act), four-eyes approval, material effects only through `io_modify`;
* deadlines, admission and failure accounting across dependencies;
* configuration-only binding, capability checks and release gates;
* contract tests that hold for every vendor.

Every item below was judged by one question: *does the managed service already do this?*
* **Yes** → **Drop** it from E2A. Use the managed feature through configuration.
* **Partly** → **Reframe**. E2A adds only the rigor layer around the managed feature.
* **No, and E2A's guarantee depends on it** → **Keep**.

How a vendor difference is handled, cheapest first: config key → optional keyword on the
public method → a base branch on a declared capability → a protected hook with a default →
a subclass.

## 2. Status of v1 items after v1.5.0

| v1 | Item | Status | Note |
|---|---|---|---|
| P22 | Claim / checkpoint state machine once | **Done** (v1.5.0 `e2a_adapters.cas`) | managed-providers now binds `e2a-adapters`. |
| P25 (stores part) | Store contract suite | **Done** (`e2a.port_contract`) | |
| P7 | Model calls not retried | **Interim done** in managed-providers (SDK retries = 2). E2A fix planned for v1.5.1. | See P7 below: reframed. |

## 3. Decisions on the v1 items

### Keep (E2A adds rigor the vendors don't)

| ID | Item | What and how, briefly |
|---|---|---|
| P1 | Bindings and profiles (`e2a.bindings`) | Composition root: profile YAML → seam → `module:Class`, lazy construction, `extends:` overlays. No vendor offers a cross-vendor binding. Lift `e2a_managed/bindings.py` and `profile.py`. |
| P2 | Config layering | Add a profile layer: `kwargs > config > env > profile > default`. Namespaced keys, resolved lazily in `RequestContext`. A `provider_options[provider]` pass-through for vendor parameters E2A doesn't model, so a new vendor feature never waits for an E2A release. |
| P3 | Scoped config (agent / step / tenant) | `scopes.agent.X`, `scopes.step.Y`, `scopes.tenant.Z` overlays applied by the base before each hook. Covers a small model for the router, a region per tenant, a delegated runtime for one agent. Replaces the `_resolve_llm_service` override. |
| P4 | Capabilities | `capabilities` / `capabilities_for(config)` on every bindable class, plus `Bindings.verify()` at startup. Bases branch on capabilities (rung 3). This is what lets E2A **use** a vendor's native feature when present. |
| P5 | Canonical model types | `Message`, `ContentPart`, `ToolCall`, `Usage`, `StopReason`, `ModelResult`, still accepting dicts. Four codecs: Anthropic Messages, OpenAI Responses, OpenAI Chat (Llama Stack, vLLM, Ollama), Gemini. Vendors each have their own types; E2A needs one. |
| P6 | Keep provider metadata; typed refusal | Stop reason, request id and the token split survive. A refusal becomes status `REFUSED`, routed to `_fallback`, never treated as an answer. |
| P15 | Delegated runtimes | A managed agent runtime (Managed Agents, AgentCore, Responses API, Agent Engine, Llama Stack) is bound as the agent's **model step**. Read tools only, enforced; host-side tool calls through `execute()` with the tenant. E2A keeps approvals and `_act`. |
| P17 (part) | Reranker port, query propagation, dimension check | A `BaseReranker` port bound to managed rerankers (Bedrock Rerank, Vertex Ranking, Azure semantic ranker). Pass the query to `_rerank`. Check that embedding and index dimensions match at construction. |
| P19 | MCP endpoint per tool + gateway auth hook + tool manifest export | Gateways (AgentCore Gateway, API Management, Apigee/IAP) do the routing and auth; E2A adds one tool-definition source. `BaseMCPServer.export_tool_manifest()` feeds gateway and managed-agent IaC. |
| P21 | `ManagedGuardrailStage` | A managed guardrail as one governance stage: fail-closed, `block | mask | monitor`, typed `FirewallRefusal`. The vendor classifies; E2A decides order and outcome. |
| P23 | Infra provisioner: neutral saga + managed-AI step + render hook | The saga is still executed by Step Functions, GCP Workflows or Logic Apps; E2A generates and tests it. |
| P24 | Release gate: profile verify + secret scan + optional live smoke | The gate decision is E2A's; vendors provide the signals. |
| P25 (rest) | `e2a[testing]`: wire replay, the swap plugin, an LLM-service contract | Offline proof that a binding honours the E2A contract. |
| P26 | Java `LlmService` parity | `LlmRequest` / `LlmResult` mirroring P5, `ModelRefusalException`, profile-based factory. |
| P27 | A2C generates bindings | The template emits `bindings_from_profile()`. Request files gain `provider_profile` and `required_capabilities`. |

### Reframe (use the managed feature; E2A adds only the rigor layer)

| ID | Was | Now |
|---|---|---|
| P7 | E2A retries model calls with its own backoff | **Vendor SDKs retry** (they honour `Retry-After`), bounded by E2A's upstream timeout and deadline. E2A adds `_classify_error()` with a default, so that: <br>(a) throttling (429) does not open the circuit; <br>(b) throttling lowers E2A's admission bucket, so callers see a typed `AdmissionTimeout` instead of vendor 429s; <br>(c) the retry budget counts SDK attempts. <br>No E2A backoff loop for models. |
| P8 | E2A fallback chains | Prefer vendor-native fallback: server-side fallbacks, cross-region inference profiles, AI-gateway failover, declared as capability `refusal_fallback:server`. `model_routes` in config only for what no vendor does: **cross-vendor** fallback, and a typed record of it. |
| P9 | E2A negotiates structured output, with a prompt-and-parse fallback | Use native constrained output where the capability exists. E2A adds **schema validation of every structured result** (rigor) and one typed error. Drop the home-grown prompt-and-parse engine; if a model lacks the feature, the capability check refuses at startup. Keep the parameter mapping only: sampling dropped where unsupported, one `reasoning_effort` vocabulary, the cache handle mapped to the vendor's cache mechanism. |
| P10 | E2A token counting | Call the vendor's count-tokens API when declared; otherwise E2A's estimate for admission only. No tokenizers in E2A. |
| P12 | E2A emits GenAI telemetry | Vendor SDKs emit OpenTelemetry spans. E2A **enriches** them with tenant, correlation id, agent, workflow step, refusal/fallback and `cost_usd` from a config price table. Fix the hard-coded `framework_version`. |
| P13 | Configurable DLP / injection patterns | The managed guardrail (P21) is the primary PII and injection control. E2A's patterns stay as a last-line egress check, with config-extensible lists. Nothing more. |
| P16 | A tool loop in `BaseAgent` | Vendors offer loops (tool runners, the Responses API, managed agents). E2A adds **governance hooks around a vendor loop** (allow-list, read-only, deadline, tenant), via P15's delegated runtime. No loop engine in E2A. |
| P18 | Async ports | Keep, but only as async variants calling the vendors' async SDKs (default: thread). No new I/O code. |
| P20 | Credential provider port | Secrets live in the vendor's secret store and identity chain. E2A adds a port that **resolves** credentials from the platform chain at call time, and a release-gate rule that refuses literal secrets in config. No secret storage in E2A. |

### Drop (the managed service already does it)

| ID | Item | Use instead |
|---|---|---|
| P14 | A default semantic-cache engine in E2A | Vendor prompt caching (prefix) and gateway semantic caching (e.g. an AI-gateway policy). E2A keeps only its existing hooks; a binding may route them to a managed cache. |
| P17 (part) | Client-side keyword leg + RRF fusion when the index lacks hybrid search | A managed index with native hybrid search (AI Search, Vertex Vector Search sparse, OpenSearch). Capability `hybrid_search` absent → `verify()` warns; no search engine logic in E2A. (managed-providers' `S3VectorsIndex` fusion should be removed or flagged accordingly.) |

## 4. New items (from the coverage review), filtered by the same rule

All are bindings of **existing** E2A ports to managed services, plus rigor E2A owns. None
re-implements a managed capability.

| ID | Area | E2A adds (keep) | Managed service does (don't replicate) | How, briefly |
|---|---|---|---|---|
| N1 | Messaging / eventing | Transactional outbox, exactly-once publication intent, idempotent consumer (claim per message), DLQ reconciliation with typed outcomes. Existing ports: `BaseOutboxSweeper`, `BaseWebhookDispatcher`, `BaseReconciliationService`, CQRS event path. | Delivery, ordering, retries, DLQ (SNS/SQS/EventBridge, Service Bus/Event Grid, Pub/Sub). | `e2a-adapters[aws|azure|gcp]` publisher/consumer adapters behind the outbox and DLQ ports. An idempotent-consumer helper that wraps a message handler in a claim from `e2a_adapters`. Contract suite in `port_contract` (duplicate delivery, poison message → DLQ → reconcile). |
| N2 | Credentials | Call-time resolution, tenant-scoped credentials, no secrets in config (gate rule). | Storage, rotation, KMS (Secrets Manager, Key Vault, Secret Manager). | P20 port plus adapters calling the managed secret API through the platform identity. |
| N3 | Identity at ingress | A verified-claims → `RequestContext` mapping (tenant, roles, subject), refusing unverified or mismatched tenants. | Token issue and verification (Cognito, Entra ID, Identity Platform, the API gateway authorizer). | `BaseIdentityResolver.resolve(claims) -> context fields`. Verification stays in the gateway. Config maps claim names per IdP. |
| N4 | Indexing from object storage | Idempotent, tenant-scoped re-indexing with `failed_keys` and reconciliation, driven by storage events. | Storage, events, and (where used) managed ingestion such as Knowledge Base sync. | Bind storage-change events (via N1) to `BaseIndexingPipeline.run()`. **Use the managed ingestion instead when no tenant or idempotency guarantee is needed.** |
| N5 | Batch inference | Idempotent batch submission (claim per batch), typed per-item outcomes, reconciliation of partial results, tenant attribution. | The batch job (Anthropic Batches, Bedrock, Azure and Vertex batch). | `BaseBatchInference`: `submit / poll / reconcile`, with transports reusing the P5 codecs. |
| N6 | Evaluation in the release gate | The gate decision, thresholds, golden and red-team sets, evidence. | Scoring (Bedrock evaluations, Vertex Gen AI evaluation, Azure AI evaluation). | Bind a managed evaluator behind `BasePipeline._run_rag_eval`; offline, the existing evaluators. |
| N7 | Agent memory | Tenant scoping, redaction before write, retention as config. | Storage and retrieval (AgentCore Memory, Managed Agents memory stores). | Bind `BaseMemoryManager` to managed memory; E2A wraps writes with the redaction stage. |
| N8 | Sandboxed code / browser tools | Treat as governed tools: allow-list, tenant, audit, never material without approval. | The sandbox (AgentCore Code Interpreter / Browser, vendor code execution). | A tool type flag (`sandboxed=True`) with the policy checks in `io_read`. |
| N9 | Document / speech pre-processing | Only the existing modality routing (`document_extractor`, `stt_provider`) and NFR limits (pages, duration). | Extraction, OCR and transcription (Textract, Document Intelligence, Document AI, Transcribe, Speech, Chirp). | Bindings only; no E2A logic. |
| N10 | AI gateways | One quota authority: config `quota_authority: e2a | gateway`. With `gateway`, E2A's admission bucket is off and gateway 429s are classified as throttling (P7). | Fleet quota, routing, failover, semantic cache. | Config key plus P7 classification. |

Considered and **not proposed**, because the managed service fully covers it:
* scheduling (EventBridge Scheduler, Cloud Scheduler: IaC only);
* model fine-tuning and registries;
* WAF and edge (already IaC in the provisioner);
* durable workflow execution (use Step Functions, Durable Functions or GCP Workflows; E2A only generates and tests the saga, P23).

## 5. Verification without accounts (applies to every item)

| Tool | What it proves | Use for |
|---|---|---|
| Emulators | Real behaviour of the managed transport, not just request shapes: DynamoDB Local, Firestore and Cosmos DB emulators (already in use), plus LocalStack Community (SQS, SNS, S3, Secrets Manager), Azurite (Blob), the Pub/Sub emulator, Step Functions Local. | N1, N2, N4, P23 |
| Wire replay through the real SDKs | Request mapping, signing, parsing, streaming, tool calls. | P5–P9, P15, N5 |
| A local open-weight model profile (Ollama or vLLM, OpenAI-compatible) | **Real model behaviour offline**: evals, refusals, tool-choice quality, latency shape. | N6, P6, P9, the projects' golden and red-team sets |
| `e2a.port_contract` extended with suites for N1, N2, N3, N5 and the LLM service | Every adapter meets the same guarantees. | all ports |
| Static IaC analysis (`tflint`, `checkov`) next to `terraform test` | IAM, network and encryption misconfigurations, offline. | P23 |

Not provable offline, and left to production: real quotas and latency, vendor content decisions,
IAM as evaluated by the cloud, private networking, regional availability.

## 6. Suggested order

1. **v1.5.1:** P7 (reframed), P6, P5, P2 (the profile layer and `provider_options` first).
2. **Next:** P1, P4, P3, P21, P15, P17 (part), P9 (reframed), P12 (reframed).
3. **Then:** N1 with its contract suite and emulators; N2/P20, N3, N10.
4. **Later:** N5, N6 with a local-model profile, N7, N8, N9, P19, P23, P24, P25, P26, P27.

Dependencies:
* P3 needs P2.
* P9 needs P4 and P5.
* N1's idempotent consumer uses the `e2a_adapters` claims (v1.5.0).
* N5 needs P5.
* N10 needs P7.
