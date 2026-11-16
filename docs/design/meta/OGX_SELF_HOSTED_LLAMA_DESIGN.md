# OGX (formerly Llama Stack) and Self-Hosted Llama — Design

### An open, self-hosted agentic API server in front of Llama or any model: the `meta_llama_aws` profile

*This document places OGX within E2A. OGX is the open-source server formerly called Llama Stack, renamed in April 2026, which now speaks the OpenAI, Anthropic and Google APIs in front of 23 inference providers. The customer runs it, so it is the route for self-hosted Llama weights (pattern 5) and a portable front for managed models. The repository's `meta_llama_aws` profile is built on the Llama Stack client and two APIs that OGX no longer lists as stable, so this document also gives the migration design. It records only what differs from the core design.*

| | |
|---|---|
| Framework | E2A, with `e2a-adapters` and `e2a-managed-providers` |
| Document type | Provider design document (HLD + LLD), Meta series |
| Author | Subham Gupta, Principal Platform Architect |
| Source baseline | `e2a-framework` `v1.7.4`; `e2a-managed-providers` `main`: `LlamaStackLLMService`, `LlamaStackEmbeddingModel`, `LlamaStackVectorIndex`, `LlamaGuardStage`, `LlamaStackPromptRegistry`, `LlamaStackResponsesRuntime`; profile `meta_llama_aws`; `distribution/run.yaml` |
| Date | 16 November 2026 (product facts checked on this date; OGX docs current release v1.5.0) |
| Status | Implemented on the Llama Stack client (offline replay). Migration to OGX v1: Documented (follow-up step). |

> **Disclaimer.** This is independent reference material for studying software architecture. It has no affiliation with, and no endorsement by, Meta, Amazon / AWS, Google, Microsoft, the OGX project or any other company or project named, and it is not a substitute for any provider's products, documentation or terms. Product names are trademarks of their owners. Product facts change: they were checked on the date shown, against the sources listed in the last section; check them again before use. Provided "as is", without warranty; not legal, compliance, security or procurement advice. Full text: [DISCLAIMER.md](../DISCLAIMER.md).

## 1. Variant identity

| | OGX |
|---|---|
| What it is | "a server-side agentic loop that speaks any frontier lab API": an HTTP server with pluggable providers. Not an inference engine: it routes to vLLM, Ollama, Bedrock and others (23 inference providers, 21 vector store backends). |
| Operator | the customer (it is open-source software, run on the customer's compute) |
| History | Llama Stack, started around Meta's Llama models; renamed **OGX** on 28 April 2026 (package `ogx`, CLI `ogx`, `OGX_*` variables, `x-ogx-*` headers, GitHub organisation `ogx-ai`); **OGX 1.0** declared the APIs stable. The Python client is now `ogx_client` (from 1.1.4 generated with OpenAPI Generator). |
| Stable APIs | `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/responses`, `/v1/models`, `/v1/files`, `/v1/vector_stores`, `/v1/batches`, `/v1/admin/tools`, `/v1/conversations`, `/v1/prompts`; semantic versioning within a major version |
| Experimental | `/v1alpha/inference/rerank`, `/v1alpha/file_processors`, `/v1alpha/interactions` (Google), admin and connectors |
| Removed in 1.0 | the agents API, fine-tuning API, `knowledge_search` tool, the TGI and Hugging Face providers. Guardrails changed from client-listed shield ids to **one server-side moderation configuration, switched on by a boolean** on the Responses API. |
| Multi-tenancy | a hard `tenant_id` partition on every stored row (default deny without one), plus ABAC within a tenant, for files, vector stores, conversations and responses |
| Pattern (core Section 3.2) | 5 (self-hosted weights behind it), or a self-run front for patterns 1 to 3 |

![Figure 1 — OGX as a self-run front: E2A on the OpenAI wire, models behind OGX's providers](images/x1_ogx_hld.png)

## 2. Profile

`meta_llama_aws` `extends` the `aws` cloud profile and runs the server as an ECS service, with Bedrock as the inference provider and pgvector for vector storage (`distribution/run.yaml`).

| Seam | Binding today (Llama Stack client) | Against OGX 1.x | Migration |
|---|---|---|---|
| `llm_service` | `LlamaStackLLMService`: `chat.completions` | stable `/v1/chat/completions` | client only: the OpenAI SDK (or `ogx_client`) at the server URL |
| `embedding_model` | `embeddings.create` | stable `/v1/embeddings` | client only |
| `vector_index` | `vector_stores` plus `vector_io.insert` / `vector_io.query` | `/v1/vector_stores` is stable; `vector_io` calls are **not** in the stable list | rewrite to vector-store files and vector-store search |
| `guardrail_stage` | `LlamaGuardStage`: `safety.run_shield` with a shield id | shields are replaced by server-side moderation | screen through the server's moderation configuration (or the cloud's guardrail), keep the fail-closed stage |
| `prompt_registry` | `prompts.retrieve` | stable `/v1/prompts` | client only |
| `delegated_runtime` | `LlamaStackResponsesRuntime`: the Responses loop | stable `/v1/responses`, with `guardrails` on, MCP tools and `file_search` | client only, add the guardrails flag |
| `run.yaml` | Llama Stack distribution format, `apis` including `agents` and `safety` | OGX distribution format | rebuild the distribution for OGX; drop `agents` |
| package extra | `llama-stack-client>=0.7` | `ogx_client` (Python 3.12+, Pydantic 2) or the OpenAI SDK | change the extra |

**Recommendation.** Migrate the profile to OGX 1.x as its own code step, and use the **OpenAI SDK** against OGX's stable OpenAI-compatible endpoints for everything but prompts. That reuses the repository's tested OpenAI wire (`OpenAIResponsesLLMService`, `ResponsesAgentRuntime`), removes a dependency on a client that is being renamed, and keeps the profile portable to any OGX inference provider. Rename the profile to `ogx_aws` and keep `meta_llama_aws` as an alias for one release.

## 3. Dispositions that differ from the core

| Responsibility | Disposition | This variant |
|---|---|---|
| Model access | REPLACE transport | OGX's OpenAI-compatible endpoints in front of the chosen provider; the model id is whatever the distribution registers |
| `DelegatedRuntime` | EXTEND | OGX's server-side Responses loop (MCP tools, `file_search`), bounded by E2A's deadline, read tools only; `_act` stays in E2A. OGX's own agents API is gone, so nothing else is bound. |
| Guardrail | COMPOSE | OGX's moderation inside the Responses loop (input, generated text and reasoning) **plus** E2A's fail-closed stage on its own model calls. Llama Guard can be the moderation backend when self-hosted. |
| Tenancy | EXTEND | OGX's tenant partition must be enabled and fed from E2A's verified tenant (an authenticated identity per tenant, not a request field). Then files, vector stores and conversations are tenant-scoped by the server. E2A's own tenant scoping stays. |
| Retrieval | REPLACE (gate) | `/v1/vector_stores` with `file_search` on the stable API, one store per tenant (as today) or the tenant partition; E2A keeps `evaluate_answer` |
| Self-hosted weights | RETAIN responsibility | the customer owns the model lifecycle, capacity, patching and licence compliance (Meta's licence) |

Everything else keeps the core default.

## 4. Sequences that differ

![Figure 2 — A read-only delegated step on OGX: server-side loop, moderation, tenant partition](images/x2_seq_ogx_responses.png)

## 5. Error mapping

| Signal | E2A result |
|---|---|
| 429 from OGX or its provider | throttled: retried within the deadline |
| 5xx, provider unreachable | transient: retried within the deadline |
| 400 (unknown model, unsupported parameter for the provider) | deterministic: no retry, `FAILED` |
| 401 / 403 (OGX authentication, tenant missing) | auth: no retry, alert. A request with no tenant sees nothing (OGX's default deny). |
| moderation flags content inside the Responses loop | `REFUSED` (governance) |
| a removed API (agents, shields) called by an old client | deterministic: the reason for the migration step |

## 6. Security, NFRs and cost

- **Operation.** The customer runs OGX: patching, scaling, high availability and its storage (SQL store, vector backend) are the customer's, unlike the managed routes.
- **Identity.** Workload identity to the inference provider (the Bedrock provider uses the AWS credential chain by default); OGX's own API authenticated per tenant. No provider key in a profile.
- **Tenancy.** Turn on the tenant partition; never run a shared OGX for several tenants without it.
- **Licence.** Self-hosted Llama weights are used under Meta's licence; check its terms for the deployment.
- **Cost drivers.** Compute for OGX and any self-hosted models (GPU), the vector backend, and provider tokens where a managed model sits behind OGX.

## 7. Evidence

| Item | Status |
|---|---|
| `meta_llama_aws` on the Llama Stack client: chat, embeddings, vector stores, shield, prompts, Responses runtime | Implemented: offline replay; the three projects' suites run on this profile |
| Behaviour against an OGX 1.x server | To validate: the `vector_io` and shield calls are outside OGX's stable API |
| Migration to OGX 1.x (OpenAI SDK, moderation, vector-store search, distribution) | Documented: recommended follow-up step |
| Live behaviour | Not covered: needs live tests |

## 8. Sources (checked 16 November 2026)

| Fact | Source |
|---|---|
| Rename, mission, migration (package, CLI, variables, headers, client) | OGX blog, "From Llama Stack to OGX: A New Name, A Sharper Mission" (28 Apr 2026) |
| OGX 1.0: stable APIs, removed APIs, providers, multi-tenancy | OGX blog, "OGX 1.0: The Open Agentic API Server is Production-Ready" |
| Stable and experimental API lists; semantic versioning | OGX docs, "API overview" |
| Guardrails as a Responses boolean; shields replaced | OGX blog, "Under the Hood: How OGX Enforces Guardrails Inside the Agentic Loop" |
| Tenant partition and ABAC | OGX blog, "Multi-Tenant AI Infrastructure with OGX" |
| `ogx_client` and its changes | OGX blog, "The OGX Python SDK Has a New Foundation" |
| Bedrock provider: credential chain, optional bearer token | OGX docs, "remote::bedrock" provider |
