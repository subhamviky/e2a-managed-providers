# E2A v1.4 — Improvement Proposal: universal abstractions for managed AI services

| | |
|---|---|
| Status | Proposal for review. Nothing here is implemented in `e2a-framework`. |
| Applies to | `e2a-framework` v1.3.0 (`reference/e2a/*`, `java/e2a-core`), `a2c-framework` generator templates |
| Evidence base | `e2a-managed-providers`: Anthropic, OpenAI, Google and Meta managed services bound to E2A v1.3.0. Its working prototypes are cited per proposal. |
| Audience | Whoever implements E2A v1.4. The document is meant to be read on its own. |

---

## 0. How to read this document

Section 1 gives the context and what the evidence showed. Section 2 sets the design rules
every proposal follows: where to be strict, where to be flexible, and how provider
differences are handled. Section 3 is the proposals. Each one is self-contained:

* **Problem**, with the file and line in E2A v1.3.0 where it shows;
* **Proposal**, with an API sketch;
* **Provider variance**, how providers that differ are handled without subclass code;
* **Strict / flexible**, what is enforced and what is left open;
* **Compatibility**, what existing code sees;
* **Acceptance**, the tests that prove it.

Section 4 maps provider differences to mechanisms. Section 5 orders the work. The
appendices list the proposed configuration keys and the prototypes in
`e2a-managed-providers` that can be lifted.

Paths such as `llm.py:209` mean `e2a-framework/reference/e2a/llm.py`, line 209, at tag `v1.3.0`.

---

## 1. Context

### 1.1 The E2A family

* **E2A** (`e2a-framework`) holds the runtime contracts. `BaseWorkflow` runs declared steps
  under one request context, deadline and idempotency claim. `BaseAgent.run()` is a fixed
  lifecycle: screen → prepare → decide → policy → build → infer → evaluate/fallback → act →
  confirm → redact. `BaseLLMAgentService` is the only path to a model: permits, RPM/TPM quota,
  circuit breaker, deadline and DLP are all base-owned, and a vendor subclass implements
  only `_dispatch_upstream_call/_stream`. The other contracts are:
  * the RAG ports (`BaseEmbeddingModel`, `BaseVectorIndex`, `BaseRAGPipeline`,
    `BaseIndexingPipeline`);
  * `BaseToolService`, `MCPToolService` and `BaseMCPServer`;
  * governance stages (`IngressStage`/`EgressStage`) with typed refusals;
  * the storage ports (idempotency claims, checkpoints, caches);
  * `BaseObservability`, `BaseInfraProvisioner` and `BasePipeline`.

  Configuration is one central registry, resolved as
  `kwargs > config dict > environment > default`. A conformance checker
  (`e2a.conformance`) enforces the inheritance rules.
* **A2C / G2C / P0** generate projects and infrastructure from request files.
* **Three projects** are built on these: order-to-cash-agentic-ai (GCP), aws-reconciliation-engine
  (AWS) and financial-settlement-platform (Azure, plus a Java service). All run on
  `SimulatedLLMService`, with cloud as Terraform tested against mock providers.

### 1.2 The exercise and its evidence

`e2a-managed-providers` bound five managed-provider combinations to E2A v1.3.0 *without
changing E2A or the projects*:

| Profile | Models | Platform services |
|---|---|---|
| anthropic_bedrock | Claude on Amazon Bedrock | Titan, S3 Vectors, Bedrock Rerank, Guardrails, Prompt Management, AgentCore, DynamoDB |
| anthropic_claude_platform_aws | Claude Platform on AWS | as above, plus MCP connector, server-side fallbacks, Claude Managed Agents |
| openai_azure | OpenAI on Azure AI Foundry | AI Search, Content Safety, APIM, Cosmos DB |
| google_vertex | Gemini on Vertex AI | Vector Search, Ranking API, Model Armor, Agent Engine, Firestore |
| meta_llama_aws | Llama via Llama Stack on AWS | Llama Guard, vector_io, DynamoDB |

Results, all offline:
* The three projects' own test suites (98 tests) pass on all five profiles. The model call
  goes through each provider's real SDK, against a replayed HTTP transport.
* 88 package tests pass.
* Java adapters on the official SDKs pass.

### 1.3 What already worked well (keep it)

* `BaseLLMAgentService`'s two-hook transport contract. Each adapter was about 100–150 lines,
  and every NFR was inherited.
* The `kwargs > config > env > default` rule. Per-call vendor overrides
  (`agent.run(..., reasoning_effort="high")`) worked with no new code.
* The v1.2 shared embedding and index ports. A managed guardrail fits as an `IngressStage`
  raising `FirewallRefusal`.
* The claim state machine is specified precisely enough to implement once for DynamoDB,
  Cosmos DB and Firestore.
* `SimulatedLLMService`, which made offline replay through real SDKs possible.
* The conformance checker, which caught real violations in the new code.

### 1.4 What had to be built outside E2A (the gaps)

Each gap below became a proposal in section 3:
* a composition root;
* profile files;
* capability checks;
* a canonical conversation format;
* typed refusals;
* a reranker port;
* per-agent model binding;
* a shared store state machine;
* managed-guardrail and delegated-runtime bases;
* a testing kit.

One defect-level finding also surfaced: **model calls are never retried** (P7).

---

## 2. Design rules for v1.4

### 2.1 Strict where correctness lives, flexible where providers differ

| Strict (enforced by base classes and conformance) | Flexible (configurable, overridable) |
|---|---|
| The lifecycle order of `run()`, `execute()`, `process_inference()` | Which class is bound to a seam |
| Tenant scoping, idempotency claims, typed outcomes | Vendor parameters, model choice, limits |
| Material effects only through `io_modify`, with a key | How a provider maps a canonical feature |
| Refusals and failures are typed, never plain text | Whether an optional feature is used |
| Every key a class reads is registered (with an escape hatch, P2) | Per-agent, per-tenant and per-call overrides |
| Capability requirements checked at startup (P4) | Fallback behaviour when a capability is absent |

### 2.2 The variation-handling ladder

When providers differ, use the lowest rung that works. Climb only when it doesn't.

1. **Configuration.** A registered key with a default. Covers model ids, limits, endpoints
   and toggles.
2. **Optional call parameter.** The same key passed as a keyword to the public method;
   the base resolves it with `resolve_config`. Covers per-request vendor behaviour.
3. **Capability-conditional base logic.** The base asks the bound object what it supports
   (P4) and branches. For example: native structured output, or prompt-and-parse when it
   isn't supported. Covers differences of kind, with automatic handling.
4. **Protected hook with a default.** The base calls `_map_x()`; the default implements the
   common case; a provider overrides only that hook. Covers wire mapping.
5. **Subclass.** Only for a genuinely different transport.

E2A v1.3 mostly offers rungs 1, 2 and 5. Most proposals add rungs 3 and 4, which are
missing today.

### 2.3 Compatibility rules

* Every proposal is additive in v1.4. A v1.3 subclass keeps working. Behaviour changes are
  opt-in by default and become default in v1.5, the same deprecation pattern as v1.2 → v1.3.
* No new mandatory abstract methods on existing classes. New hooks have defaults.
* Conformance gains rules only as warnings in v1.4.

---

## 3. Proposals

### A. Composition and configuration

#### P1 — `e2a.bindings`: the composition root and profiles

**Problem.** E2A has no binding concept. Each project constructs its seams in code and
hard-wires `SimulatedLLMService`: `build_o2c_llm()`, `build_triage_llm()`,
`build_audit_llm()`. O2C even types the constructor parameter as `SimulatedLLMService`. So
changing provider means changing code in every project.

**Proposal.** Add a module `e2a.bindings`:

```python
from e2a.bindings import bindings_from_profile

b = bindings_from_profile()          # name from E2A_PROVIDER_PROFILE, or a path / name argument
b.llm_service, b.embedding_model, b.vector_index, b.reranker, b.guardrail_stage, ...
config = b.config(tools=..., governance_engine=...)   # the dict for execute()/run()
b.verify()                            # P4: fail at startup on missing capabilities
```

* A **profile** is a YAML file with `name`, `provider`, `cloud`, `features`,
  `bindings: {seam: "module:Class"}` and `config: {...}`.
* **Profile inheritance** uses `extends: <profile>` with deep-merge overlays. It enables
  the layering *E2A → provider profile → application profile* (section 4.2).
* **Lazy construction.** Each seam is built on first access, so only the SDKs of the seams
  actually used must be installed.
* **Uniform construction convention** for every bindable class:
  `Class(config: dict, client: Any = None)`. `client` injection exists for tests and for
  shared clients. A class that cannot take that signature exposes
  `@classmethod from_config(cls, config)`.
* **Placeholders.** Profile values support `${ENV}` / `${ENV:-default}`. An unset
  placeholder with no default is *dropped*, so the key falls through to E2A's own env and
  default resolution.

**Provider variance.** A new provider is a new set of classes plus a profile file. No E2A
change.

**Strict / flexible.** Unknown seam names and unknown profile fields are errors. Any class
may be bound.

**Compatibility.** Purely additive. Projects adopt it by replacing one factory.

**Acceptance.**
* Every profile in a test fixtures folder binds.
* Lazy construction makes no network call.
* `extends` overlays merge as specified.
* An unknown seam raises.

**Prototype:** `e2a_managed/bindings.py`, `profile.py`.

#### P2 — Configuration layering, namespacing and the vendor escape hatch

**Problems.**
1. **The config dict beats env** (`config.py:239-246`). A value written in a profile
   (config layer) cannot be overridden by an environment variable without placeholder
   tricks.
2. **One flat global registry.** The managed providers needed about 50 vendor keys
   (`aws_region`, `azure_openai_endpoint`, `llama_shield_id`, …), all global.
   `RequestContext.begin()` resolves **every** registered key on every request
   (`context.py:59`). That costs time and copies unrelated settings into every request.
3. **Unregistered keys raise `KeyError`** (`config.py:236-238`). That is right as a typo
   guard, but it blocks passing a new vendor parameter until code registers it.

**Proposal.**
* **A profile layer:** `kwargs > config > env > profile > default`. `resolve_config` gains
  an optional `profile` mapping (`Bindings` supplies it). Profile literals become
  overridable by env with no placeholders.
* **Namespaced keys:** `ConfigKey(namespace="provider.anthropic", ...)`. `RequestContext`
  resolves eagerly only namespace `core` plus the namespaces a request touches, and the
  rest lazily on `ctx.setting(key)`. `unknown_config_keys()` reports per namespace.
* **The escape hatch:** one registered key, `provider_options: Dict[str, Dict[str, Any]]`,
  e.g. `{"anthropic": {"betas": [...], "metadata": {...}}}`. A vendor transport merges
  `provider_options[self.provider]` into its request as-is. It works per call
  (`run(..., provider_options={...})`) like every other key.
* **Mode presets:** `ConfigPreset("hosted")` sets `llm_max_latency=60`,
  `context_budget=150000`, `max_output_tokens=4096`. Today the defaults suit only the
  simulator (`llm_max_latency=3.0`, `context_budget=8192`). A profile selects a preset with
  `preset: hosted`.

**Strict / flexible.** Registered keys stay mandatory for anything E2A itself reads.
`provider_options` is the one sanctioned untyped channel, and it is never read by base
classes.

**Compatibility.** The order for existing layers is unchanged; the profile layer is new
and sits below env.

**Acceptance.**
* Env overrides a profile literal.
* `RequestContext.begin` resolves only core keys eagerly.
* `provider_options` reaches the wire unchanged.
* Unknown top-level keys are still reported.

#### P3 — Scoped configuration: per agent, per step, per tenant

**Problem.** One config dict serves a whole workflow. Common real needs require code today:
* a cheap fast model for the router and a strong model for the specialists;
* lower reasoning effort for classification;
* a delegated runtime for one agent only;
* a different region or model for a tenant with data-residency rules.

The delegated runtime had to override `_resolve_llm_service` (`_propagation.py:90`).
`tenant_routing_map` already exists, but only for tool endpoints.

**Proposal.** Add scoped overlays resolved by the base before each hook call:

```yaml
config:
  model_id: claude-opus-5-5
  scopes:
    agent:
      RouterAgent:   {model_id: claude-haiku-5-5, reasoning_effort: low}
      KnowledgeAgent: {llm_service: delegated_runtime}      # a bound seam name
    step:
      act:           {max_latency: 10}
    tenant:
      eu-customer:   {aws_region: eu-central-1, model_id: eu.anthropic.claude-opus-5-5}
```

* **Resolution order:** `kwargs > scope(agent) > scope(step) > scope(tenant) > config > env > profile > default`.
* `BaseAgent.run()` applies `scopes.agent[agent_name]`. `BaseWorkflow` applies
  `scopes.step[step_name]`. `RequestContext.begin` applies `scopes.tenant[tenant_id]`.
* `llm_service` in a scope may name a seam from `Bindings`, which replaces the
  `_resolve_llm_service` override.

**Provider variance.** Mixed-provider deployments become configuration, e.g. a router on
a cheap model of one vendor and a specialist on another.

**Strict / flexible.** Scopes may only set registered keys. Lifecycle keys
(`idempotency_*`, `tenant_id`) cannot be scoped.

**Compatibility.** No `scopes` key means behaviour is unchanged.

**Acceptance.**
* The router and a specialist in one workflow call different models.
* A tenant scope changes the region.
* A scoped lifecycle key is refused.

#### P4 — Capabilities: declared, verified, and used for conditional handling

**Problem.** Platforms differ even for the same model. For example, Claude on Bedrock has
no Anthropic MCP connector, while Claude Platform on AWS has one. E2A cannot tell, so a
deployment fails on its first request instead of at startup. Separately,
`model_capability_registry` (used by `LLMOnlyAgent._select_model`, `agents.py:318`) already
maps modality to model: a capability concept that is not generalised.

**Proposal.**
* `class Capability(str, Enum)`: tool_use, structured_output, streaming, prompt_caching,
  reasoning_effort, refusal_fallback, mcp_connector, batch, embeddings, vector_search,
  hybrid_search, rerank, guardrails, prompt_registry, managed_agent_runtime,
  idempotency_store, checkpoint_store, telemetry, …
* Every bindable class declares `capabilities: frozenset` or
  `capabilities_for(config) -> frozenset` (platform-dependent).
* `Bindings.verify(required)` raises `CapabilityError` listing what is missing. The profile
  field `features` and the key `managed_features` supply `required`.
* **Base classes consult capabilities to choose behaviour** (rung 3 of the ladder): P9
  structured output, P8 fallback, P11 streaming tool calls, P17 hybrid search.
* `model_capability_registry` becomes one use of the same mechanism: model id → capability
  set (vision, audio, document, long_context).

**Acceptance.**
* A profile requiring `mcp_connector` on a Bedrock binding fails `verify()`.
* A base branch is exercised for a binding with and without the capability.

**Prototype:** `e2a_managed/capabilities.py`, `Bindings.verify`.

---

### B. The LLM service contract

#### P5 — Canonical model types

**Problem.** E2A messages are untyped dicts with `role`/`content`.
`_compile_tool_schemas` normalises tool *declarations* (`llm.py:372`), but the rest of the
conversation has no canonical shape:
* no tool-call shape;
* no tool-result message;
* no content parts (images, documents);
* no usage split;
* no stop reason.

Every provider adapter had to invent one, so four adapters means four inventions.

**Proposal.** Add `e2a.llm_types`. These are dataclasses that also accept and produce
plain dicts, so the existing dict-based code keeps working:

```python
Message(role: "system"|"user"|"assistant"|"tool", content: str | list[ContentPart],
        tool_calls: list[ToolCall] = [], tool_call_id: str | None = None)
ContentPart = TextPart(text) | ImagePart(media_type, data|url) | DocumentPart(media_type, data|file_id)
ToolCall(id: str, name: str, arguments: dict)           # arguments always a parsed dict
ToolDeclaration(name, description, parameters: JSONSchema, strict: bool = False)
Usage(input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0)
StopReason = end_turn | tool_use | max_tokens | stop_sequence | refusal | pause | error
ModelResult(text, tool_calls, usage: Usage, stop_reason: StopReason, model: str,
            provider_request_id: str | None, raw_metadata: dict)
```

* `_dispatch_upstream_call` returns a `ModelResult` or the legacy dict. The base accepts both.
* A **codec registry**, `e2a.codecs`, holds pure mapping functions for the four wire
  families that cover the market today: Anthropic Messages, OpenAI Responses, OpenAI Chat
  Completions (also Llama Stack, vLLM, most OpenAI-compatible servers), and Gemini. A
  provider transport then becomes "pick codec + SDK call".

**Strict / flexible.** Tool arguments are always a parsed dict; providers that send JSON
strings are parsed in the codec, never string-matched. Content parts are optional; plain
strings still work.

**Acceptance.** For each codec, a conversation with text, a tool call, a tool result and
an image survives a round trip to the wire and back.

**Prototype:** `e2a_managed/codec.py`; wire decoders and encoders in `offline.py`.

#### P6 — Keep provider metadata; typed stop reasons and refusals

**Problem.**
* `process_inference` overwrites `payload['metadata']` with `{model_id, tokens_used}`
  (`llm.py:215`). Stop reason, provider request id and the input/output/cache token split
  are lost.
* `_format_and_validate_output` (`llm.py:406`) knows only text and tool calls. A provider
  **refusal** (a safety decline returned as a normal 200 response) looks like an answer.

**Proposal.**
* `payload['metadata']` = base fields ∪ `ModelResult.raw_metadata`; base fields win on
  conflict. Add top-level `stop_reason`, `usage` (Usage dict) and `provider_request_id`.
* `stop_reason == refusal` → `ModelRefusal(provider, model, category)`, a typed exception
  in `e2a.core`. `process_inference` returns `{'status': 'REFUSED', ...}`, distinct from
  `FAILED`, and `BaseAgent` routes it to `_fallback` (not `_handle_error`). A refusal is a
  policy outcome, not a fault.
* `stop_reason == max_tokens` → `payload['truncated'] = True`, so agents can decide.

**Acceptance.**
* Each codec's refusal fixture yields `REFUSED`, and the agent runs `_fallback`.
* Metadata keeps the provider request id.

#### P7 — Error classification and retries for model calls (defect)

**Problem.** The upstream call runs through `self._resilience.call(_upstream, deadline=deadline)`
(`llm.py:209`) **without `max_retries`**, so it is called with `max_retries=0`. A model call
is never retried by E2A. Throttling (429) and transient 5xx are not separated from real
faults:
* `Retry-After` is ignored;
* a burst of 429s opens the circuit breaker exactly like an outage.

(`e2a-managed-providers` disabled SDK retries assuming E2A retries, so today no layer
retries. See the note in section 6.)

**Proposal.**
* A new protected hook with a default, `_classify_error(error) -> ErrorClass`:

  ```python
  ErrorClass(kind: "transient"|"throttled"|"overloaded"|"deterministic"|"auth"|"context_overflow"|"refusal",
             retry_after: float | None = None)
  ```

  The default maps `asyncio.TimeoutError` → transient, `ValueError/TypeError` →
  deterministic, and HTTP-status-bearing exceptions (`status_code` attribute, the common
  SDK convention) as 429 → throttled (reading a `retry-after` header when present),
  529/503 → overloaded, 5xx → transient, 401/403 → auth, 400 with a context-length marker
  → context_overflow.
* New keys: `llm_max_retries` (default 2), `llm_retry_on` (default
  `[transient, throttled, overloaded]`), `circuit_counts` (default `[transient, overloaded]`,
  so **throttling does not open the circuit**).
* `ResiliencePolicy.call` takes `retryable=` and a new `delay_for=`, so the backoff
  honours `retry_after` and stays bounded by the deadline.
* A `throttled` result also lowers the in-memory RPM/TPM bucket (adaptive admission), so
  E2A's typed `AdmissionTimeout` takes over from provider 429s.
* The same classifier serves `BaseToolService` and the RAG ports.

**Provider variance.** A provider overrides only `_classify_error` when its SDK exposes
errors differently (rung 4).

**Acceptance.**
* A 429 with `retry-after: 1` is retried after about 1 s and does not count toward the circuit.
* A 400 is not retried.
* The total time never exceeds the request deadline.

#### P8 — Model routing and fallback chains (configuration)

**Problem.** Fallback behaviour is ad hoc per provider. Some platforms offer server-side
fallback, others don't. Reasons to fall back differ: refusal, overloaded, context overflow.

**Proposal.** A registered key `model_routes`:

```yaml
model_routes:
  - {on: [refusal],          to: claude-opus-4-8}
  - {on: [overloaded],       to: claude-sonnet-5-5, max_attempts: 1}
  - {on: [context_overflow], to: gemini-2.5-pro, llm_service: secondary}   # cross-provider via a bound seam
```

The base applies the first matching route after classification (P7). The result records
`fallback_from`. With capability `refusal_fallback:server`, the transport may delegate to
the provider's native fallback and the base skips its own.

**Acceptance.**
* A refusal on the primary model is answered by the route target.
* The chain stops at `max_attempts`.

#### P9 — Capability-negotiated request features (automatic handling)

**Problem.** The same intent maps differently per provider, and some providers lack a
feature entirely:
* **Structured output:** native schema-constrained output (Claude, OpenAI, Gemini) vs.
  prompt-and-parse for others.
* **Sampling parameters:** current Claude and OpenAI reasoning models reject `temperature`;
  Gemini and Llama accept it.
* **Reasoning depth:** `output_config.effort`, `reasoning.effort`, `thinking_level` or
  `thinking_budget`.
* **Prompt caching:** an explicit `cache_control` breakpoint, a `prompt_cache_key` routing
  hint, or a named cached-content resource. E2A decides *what* to cache
  (`_apply_kv_caching`, `llm.py:359`) but cannot express *how*.
* **Tool choice and parallel tool calls**, which not every model supports.

**Proposal.** Base-owned negotiation, using P4 plus small per-feature hooks with defaults:

```python
class BaseLLMAgentService:
    features: Mapping[str, str] = {}   # e.g. {"structured_output": "native", "sampling": "unsupported",
                                       #       "reasoning": "effort", "prompt_cache": "breakpoint"}
    def _apply_structured_output(self, request, schema): ...   # default: system instruction + JSON parse/validate
    def _apply_reasoning(self, request, effort): ...           # default: no-op
    def _apply_cache_handle(self, request, handle): ...        # default: no-op
```

* `response_schema` with `structured_output` absent or `prompt` → the base adds a schema
  instruction, parses and validates the output, and on failure retries once with the
  validation error (configurable). Otherwise the transport's native mapping is used.
* `temperature` is dropped by the base when `features["sampling"] == "unsupported"`, and
  the log notes `param_dropped`.
* `reasoning_effort` uses one vocabulary (`minimal|low|medium|high|max`); the transport
  maps it.
* Result: an agent states *what* it needs. The base and the declared features decide
  *how*, so no agent contains `if provider == ...`.

**Acceptance.** The same agent with `response_schema` returns `structured_output` both on
a native-capable binding and on a prompt-only binding.

#### P10 — Token counting and context budget

**Problem.** `_estimate_tokens` is `len/4` (`llm.py:329`). That is fine for quotas, but
wrong enough to trim context badly near the limits of 200K–1M context windows, and
providers tokenise differently.

**Proposal.**
* An optional hook `_count_tokens(messages, tools, model_id) -> int | None`; providers with
  a counting API implement it.
* The base uses it when `context_budget_mode: exact` and the call is within 20% of the
  budget, otherwise the estimate.
* A per-model `context_window` in the model capability registry (P4) replaces a single
  global `context_budget`.

#### P11 — Streaming events, not only text

**Problem.** `process_inference_stream` yields text chunks only (`llm.py:261`, `:668`).
Tool calls, usage and stop reason cannot be streamed, so a streaming agent cannot use tools.

**Proposal.**
* `process_inference_events()` yields `TextDelta | ToolCallStart | ToolCallDelta |
  ToolCallEnd | UsageUpdate | Stop`.
* The existing text stream becomes a projection of it.
* DLP holdback applies to `TextDelta` only.
* Partial tool-call JSON is validated against the schema before `ToolCallEnd`. On failure
  it is treated as `invalid_tool_input`, never executed.

#### P12 — Telemetry for model calls, with cost

**Problems.**
* `_emit_metric` is a no-op by default (`llm.py:476`).
* `BaseObservability` hard-codes `'framework_version': 'e2a-v1.0'` (`observability.py:47`).
* Cost is computed only by the simulator.

**Proposal.**
* The base emits OpenTelemetry GenAI semantic-convention attributes for every inference
  (provider, model, input and output tokens, cache tokens, stop reason, latency, fallback,
  tenant).
* A registered key `model_price_table` (`{model_id: {input_per_mtok, output_per_mtok,
  cache_read_per_mtok}}`) gives `cost_usd` per call and per request.
* `framework_version` comes from `e2a.__version__`.

#### P13 — Configurable DLP and injection patterns

**Problem.** `_INJECTION_PATTERNS` and `_DLP_PATTERNS` are class constants
(`llm.py:80`, `:86`). Adding a pattern (an IBAN, a national ID) means a subclass.

**Proposal.**
* Registered keys `dlp_patterns` and `injection_patterns` take lists of `{name, regex,
  validator?}`, added to the built-ins. `dlp_patterns_replace: true` replaces them instead.
* Validators are named (`luhn`, `iban_mod97`); no code in config.
* A per-tenant scope (P3) is allowed.

#### P14 — A default semantic cache

**Problem.** `_check_semantic_cache` / `_store_semantic_cache` are no-op hooks
(`llm.py:386`). Every adopter would write the same code.

**Proposal.**
* A default implementation, active when `semantic_cache_store` (a bound `BaseCacheStore`)
  and an embedding model are bound. It is tenant-scoped, uses a cosine threshold
  `semantic_cache_threshold` (default 0.95) and a TTL, and is never used with tools (as today).
* Off unless both seams are bound.

---

### C. Agents and workflows

#### P15 — Delegated runtimes inside the agent lifecycle

**Problem.** Managed agent runtimes run the reasoning loop on the provider side: Claude
Managed Agents, Bedrock AgentCore Runtime, the OpenAI Responses API with hosted tools,
Vertex AI Agent Engine, Llama Stack Responses. Binding one *instead of* `BaseAgent` would
move idempotency, approvals and material effects out of E2A.

**Proposal.** Upstream two classes:
* `DelegatedRuntime(BaseLLMAgentService)`: one managed session per inference.
  * `bind_tool(name, tool, schema)` refuses a tool with `material = True`.
  * Host-side tool calls run through the tool's `execute()` with the request tenant.
  * The run is bounded by `delegated_max_turns` and the request deadline.
* Binding it to one agent uses the P3 scope (`scopes.agent.X.llm_service`). No
  `DelegatedAgent` subclass is needed once P3 exists.

**Strict.** A material tool can never reach a delegated runtime. This is a conformance rule.

**Prototype:** `e2a_managed/delegated.py`, plus the provider runtimes under
`providers/*/delegated_runtime.py`.

#### P16 — A governed tool-use loop in `BaseAgent` (optional)

**Problem.** `BaseAgent` makes one inference per run. An agent that lets the model choose
read tools must hand-write the loop, and so risks bypassing `io_read`, the allow-list and
the deadline.

**Proposal.**
* With `allowed_tools` declared and `agent_tool_loop: true`, `run()` runs the
  infer → tool calls → `io_read.call()` → infer loop itself. It is bounded by
  `agent_max_tool_turns` and the deadline, and parallel tool calls run concurrently.
* Only read tools are offered to the model.
* Material actions remain `_act`'s job, after evaluation, unchanged.
* Tool results are appended with P5 types, so the loop is provider-neutral.

---

### D. Retrieval

#### P17 — Reranker port, query propagation, index contract

**Problems.**
* `retrieve()` calls `self._rerank(results, config, **kwargs)` without the query
  (`rag.py:79`), so a cross-encoder reranker cannot score pairs.
* There is no reranker port, although managed rerankers are common (Bedrock Rerank,
  Vertex Ranking, Azure semantic ranker).
* Hybrid search is part of the contract, but vector-only indexes cannot declare that they
  lack it.
* Nothing checks that embedding dimensions match the index.

**Proposal.**
* `BaseReranker.rerank(query, hits, top_n) -> hits` is a port like
  `BaseEmbeddingModel`. `BaseRAGPipeline` gains `reranker`, and the default `_rerank` uses
  it when bound, with the query passed as `kwargs['query']`.
* `BaseVectorIndex.capabilities` includes `hybrid_search` or not. When absent, the base
  runs the keyword leg itself over an over-fetched candidate set and fuses with reciprocal
  rank fusion (rung 3, automatic).
* `BaseEmbeddingModel.dimensions` and `BaseVectorIndex.dimensions`: `RAGPipeline` refuses a
  mismatch at construction, as it already does for model ids (`rag.py:219`).
* `tenancy_model: POOL|SILO` decides the namespace strategy (metadata filter vs. index per
  tenant), in place of the hard-coded `f'{prefix}_{tenant}'`.

#### P18 — Async ports

**Problem.** The LLM and tool paths are async. The embedding model, vector index, prompt
registry and governance stages are sync, so managed calls in them block the event loop or
need thread hops.

**Proposal.** Each port gains `async a<method>()`. The default runs the sync method in a
thread; a native-async provider overrides it. Base classes call the async form inside
async paths. Sync callers are unchanged.

---

### E. Tools, MCP and credentials

#### P19 — MCP endpoints per tool, gateway auth, tool manifest export

**Problems.**
* `MCPToolService` reads one global `mcp_server_url`, which makes several MCP servers per
  service awkward.
* Managed gateways (AgentCore Gateway, API Management, Apigee/IAP) need per-gateway auth
  headers.
* The tools a service exposes are declared in `BaseMCPServer.registered_tools`, but
  managed agent definitions and gateway targets re-declare them by hand.

**Proposal.**
* `MCPToolService(endpoint=..., auth=...)` per instance, with `mcp_server_url` as the default.
* An `_auth_headers(endpoint, config, kwargs)` hook with a default bearer implementation.
* `BaseMCPServer.export_tool_manifest(fmt)` writes the registry to the formats provider
  layers consume (a JSON tool list, an MCP `tools/list` snapshot), so managed-agent and
  gateway IaC are generated from one source of truth.

#### P20 — A credential provider port

**Problem.** `auth_token` is a config key (`tools.py`). In managed deployments, credentials
come from the platform's chain (IAM role, managed identity, ADC, workload identity), and
must never sit in config.

**Proposal.**
* `BaseCredentialProvider.headers(target, scope) -> dict` and `token(scope) -> str`.
* The default reads `auth_token` (today's behaviour). Bindings supply platform providers.
* The release pipeline's static stage flags literal secret values in config (P24).

---

### F. Governance

#### P21 — `ManagedGuardrailStage`

**Problem.** Every managed guardrail (Bedrock Guardrails, Azure Content Safety Prompt
Shields, Model Armor, Llama Guard) needs the same wrapper: field selection, block vs.
mask, typed refusal, and what to do when the service is down.

**Proposal.** Upstream an `IngressStage` subclass with one hook, `_screen(text, ctx) ->
Verdict(allowed, categories, sanitized)`, and keys:
* `guardrail_fields` (default `["query"]`);
* `guardrail_fail_open` (default **false**, fail closed);
* `guardrail_mode: block|mask|monitor` (`monitor` logs without refusing, for rollout).

A matching `ManagedEgressGuardrailStage` covers output screening.

**Prototype:** `e2a_managed/guardrails.py`.

---

### G. State

#### P22 — One claim and checkpoint state machine over conditional writes

**Problem.** `BaseIdempotencyStore` and `BaseCheckpointStore` specify a precise state
machine (`ports.py:254-320`). Each backend re-implements it, which is the hard and
error-prone part.

**Proposal.** Upstream a `ConditionalWriteStore` mixin with two primitives:
* `_read(key) -> (record, version)`;
* `_write_if(key, record, expected_version) -> bool`.

On top of them, `ConditionalIdempotencyStore` and `ConditionalCheckpointStore` implement
`claim/complete/fail` and `save/load/decide` once. They handle lease takeover, fingerprint
conflict, replay, recovery, a bounded number of compare-and-swap retries, and wall-clock
leases. A backend becomes about 30 lines, and one contract test suite covers all of them
(P26).

**Prototype:** `e2a_managed/stores.py`, with DynamoDB, Cosmos DB and Firestore subclasses.

---

### H. Control plane

#### P23 — `BaseInfraProvisioner`: neutral defaults and the managed-AI step

**Problems.**
* `_generate_saga_orchestrator` defaults to AWS States Language; the docstring says
  non-AWS subclasses must override.
* There is no step for managed AI resources (model access, guardrail, index, gateway).
* There is no standard output for the IaC tool.

**Proposal.**
* The default saga is a neutral, logical description. Cloud renderers (ASL, GCP Workflows,
  Logic Apps) are selected by `cloud` from a registry, and `GCPInfraProvisioner` becomes
  one of them.
* An eighth hook, `_define_managed_ai_resources(config)`, defaulting to `{}`.
* A `_render(manifest) -> files` hook, defaulting to Terraform `*.auto.tfvars.json`.
  Provisioning stays manifest-only; CI applies.

#### P24 — `BasePipeline`: configurable stages and profile verification

**Problem.** `required_artifact_matrix` is fixed in `__init__` (`pipeline.py:38`). No stage
verifies the deployment profile or scans config for literal secrets.

**Proposal.**
* `artifact_matrix` becomes a registered key.
* The static stage runs `Bindings.verify()` for the release profile and flags literal
  secret values.
* An optional `live_smoke` stage, enabled by `release_live_smoke: true`, makes one real
  call per bound seam.

---

### I. Testing kit

#### P25 — `e2a[testing]`

**Problem.** The most valuable verification in this exercise was the projects' own suites
running unchanged behind real provider SDKs, plus contract suites for stores. That
machinery lives outside E2A, so every adopter would rebuild it.

**Proposal.** Ship as an extra:
* `ReplayTransport` and `offline_client(provider)`: a real SDK with a replayed wire,
  content supplied by a `SimulatedLLMService`;
* the pytest plugin `e2a.testing.swap` (`E2A_PROVIDER_PROFILE` + `E2A_SWAP_FACTORIES`);
* contract suites: `IdempotencyStoreContract`, `CheckpointStoreContract`,
  `LLMServiceContract` (unary, stream, tools, refusal, DLP) and `GuardrailStageContract`.
  A backend author subclasses the suite and passes their instance.

**Prototype:** `e2a_managed/offline.py`, `swap.py`, `pytest_plugin.py`,
`tests/test_stores_contract.py`.

---

### J. Java parity

#### P26 — e2a-core (Java) `LlmService` contract

**Problem.** `LlmService.infer(List<Map<String,String>>, RequestContext) -> LlmResponse(text,
tokensUsed)` has no tools, no streaming, no stop reason, no refusal and no structured
output. Java services cannot adopt managed features without leaving the contract.

**Proposal.**
* Records `LlmRequest(messages, tools, responseSchema, settings)` and
  `LlmResult(text, toolCalls, usage, stopReason, providerRequestId)`, mirroring P5.
* A default method keeps today's `infer(...)` signature.
* `ModelRefusalException` lives in core.
* Settings resolution `ctx.setting(key) > env > profile > default` (`ManagedSettings` in the
  prototype).
* `ManagedLlmServices.fromProfile(name)` as the Java composition root, with the same
  profile names as Python.

**Prototype:** `e2a-managed-providers/java`.

---

### K. A2C

#### P27 — Generate bindings, not simulators

**Proposal.**
* The `services/llm.py` template emits `bindings_from_profile().llm_service`, with
  `SimulatedLLMService` only under the `simulated` profile, which becomes a profile like
  any other.
* The request schema gains `provider_profile` and `required_capabilities`.
* The generated CI runs the project suite on each target profile with the swap plugin.
* The landing-zone generator reads the profile's cloud, so a project is no longer tied to
  one cloud by its request file.

---

## 4. Provider variance → mechanism (summary)

### 4.1 Matrix

| Difference between providers | Mechanism (ladder rung) | Proposal |
|---|---|---|
| Model ids, endpoints, regions, limits | config + env + profile | P1, P2 |
| A vendor feature only one provider has | `provider_options[provider]` per call | P2 |
| Different model per agent / step / tenant | scoped config | P3 |
| Feature present on one platform, absent on another | capability declaration + `verify()` + base branch | P4 |
| Wire formats | codec registry + canonical types | P5 |
| Refusal semantics | typed `ModelRefusal`, `REFUSED` status, `_fallback` | P6 |
| Error / throttling semantics | `_classify_error` hook + config retry policy | P7 |
| Fallback (server-side vs client-side) | `model_routes` + capability | P8 |
| Structured output / sampling / reasoning / cache mapping | declared `features` + small hooks with defaults | P9 |
| Tokenisers, context windows | `_count_tokens` hook + model capability registry | P10 |
| Native hybrid search vs vector-only | index capability → base keyword leg + RRF | P17 |
| Sync vs async SDKs | async port variants with thread default | P18 |
| Gateway auth schemes | `_auth_headers` hook | P19 |
| Credential chains | credential provider port | P20 |
| Guardrail block vs mask vs monitor | `guardrail_mode` | P21 |
| Database conditional-write primitive | two-method mixin | P22 |
| Cloud saga syntax / IaC output | renderer registry, `_render` hook | P23 |

### 4.2 The three layers this enables

```
E2A core           contracts, canonical types, bindings, capabilities, base-owned negotiation
  └─ provider layer   e2a-provider-<vendor> / e2a-cloud-<cloud>: transports, codecs, platform
                      services, base profiles (anthropic_bedrock.yaml), tool-manifest → managed-agent IaC
       └─ application layer   <app>.yaml  extends: anthropic_bedrock
                              scopes (router on a small model), prompts, guardrail policy,
                              eval thresholds, which agents delegate; NO provider classes in app code
```

The rule that keeps applications portable is that **application code depends on
capabilities and canonical types, never on provider classes**. A2C enforces it at
generation time (P27), and conformance can warn on a direct provider import inside an agent.

---

## 5. Order of work

| Release | Proposals | Why this order |
|---|---|---|
| v1.4.0 (foundation) | **P7** (defect), P5, P6, P1, P2, P4 | Correctness first. Canonical types and bindings are prerequisites for everything else. |
| v1.4.1 | P3, P9, P8, P17, P21, P22 | Automatic handling on top of capabilities. Each is independent once v1.4.0 lands. |
| v1.4.2 | P15, P16, P19, P20, P12, P13, P25 | Runtimes, tools, credentials, telemetry and the testing kit. |
| v1.5.0 | P10, P11, P14, P18, P23, P24, P26, P27 | Larger surface changes (streaming events, async ports, Java contract, A2C). Opt-in defaults from v1.4 become defaults. |

Dependencies:
* P3 needs P2.
* P8 needs P6 and P7.
* P9 needs P4 and P5.
* P15 needs P3.
* P16 needs P5.
* P25 needs P1.
* P27 needs P1 and P4.

---

## 6. Notes for the implementer

* **Testing.** Every proposal can be verified offline with the replay approach (P25). Use
  the four codecs as the parametrisation axis, and the three projects' suites as the
  regression oracle: they must keep passing unchanged on every profile.
* **The interim defect (P7).** Until P7 ships, adapters should *keep* SDK-level retries
  (the SDKs honour `Retry-After`), bounded by E2A's upstream timeout. Leave E2A's breaker
  as is.
* **Conformance additions,** warnings in v1.4:
  * a material tool bound to a delegated runtime;
  * a provider class imported in an agent module;
  * an unregistered key read through `resolve_config` outside `provider_options`.
* **Documentation.** `config_reference_markdown()` and `env_example()` must include
  namespaces and the profile layer, so the generated `.env.example` stays the single source.

---

## Appendix A — Proposed configuration keys

| Key | Default | Proposal |
|---|---|---|
| `provider_profile` (`E2A_PROVIDER_PROFILE`) | – | P1 |
| `managed_features` | `[]` | P4 |
| `provider_options` | `{}` | P2 |
| `scopes` | `{}` | P3 |
| `llm_max_retries` | 2 | P7 |
| `llm_retry_on` | `[transient, throttled, overloaded]` | P7 |
| `circuit_counts` | `[transient, overloaded]` | P7 |
| `model_routes` | `[]` | P8 |
| `reasoning_effort` | – | P9 |
| `structured_output_retry` | 1 | P9 |
| `context_budget_mode` | `estimate` | P10 |
| `model_price_table` | `{}` | P12 |
| `dlp_patterns`, `injection_patterns`, `dlp_patterns_replace` | `[]`, `[]`, `false` | P13 |
| `semantic_cache_threshold` | 0.95 | P14 |
| `delegated_max_turns` | 8 | P15 |
| `agent_tool_loop`, `agent_max_tool_turns` | `false`, 6 | P16 |
| `tenancy_model` | `POOL` | P17 |
| `guardrail_fields`, `guardrail_fail_open`, `guardrail_mode` | `["query"]`, `false`, `block` | P21 |
| `artifact_matrix`, `release_live_smoke` | current list, `false` | P24 |

## Appendix B — Prototypes to lift from `e2a-managed-providers`

| Proposal | File |
|---|---|
| P1, P4 | `e2a_managed/bindings.py`, `profile.py`, `capabilities.py` |
| P2 (keys) | `e2a_managed/config_keys.py` |
| P5 | `e2a_managed/codec.py` |
| P6, P8 (partial) | `e2a_managed/managed_llm.py` (`ModelRefusal`, fallback retry) |
| P15 | `e2a_managed/delegated.py`, `providers/*/delegated_runtime.py` |
| P17 | `e2a_managed/managed_rag.py`, `clouds/aws/bedrock.py` (`S3VectorsIndex` keyword fusion) |
| P19 | `e2a_managed/mcp_gateway.py` |
| P21 | `e2a_managed/guardrails.py` |
| P22 | `e2a_managed/stores.py`, `tests/test_stores_contract.py` |
| P24 | `e2a_managed/release.py` |
| P25 | `e2a_managed/offline.py`, `swap.py`, `pytest_plugin.py` |
| P26 | `java/src/main/java/com/subhamviky/e2a/managed/*` |
