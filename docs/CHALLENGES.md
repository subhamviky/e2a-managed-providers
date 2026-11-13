# Challenges, gaps and what is not proven

## Not proven, by design

* **No live call was made.** The adapters are exercised through the real provider SDKs
  with the HTTP layer replaced. That proves request building, auth signing (SigV4 for
  Bedrock), parsing, streaming and tool-call mapping as the SDKs implement them. It does
  not prove service-side behaviour: quotas, latency, model quality, content-filter
  decisions, or regional availability. `examples/live_smoke.py` and the `live_smoke`
  input of the deploy workflows are the first step once credentials exist.
* **Wire fixtures can drift.** The replay encoders follow each provider's public API
  reference. Pin SDK versions; an SDK upgrade plus `e2a_managed/offline.py` is what to
  re-check.
* **Model quality.** The projects' golden and red-team evals only mean something against
  a real model. Offline they run against the deterministic rules.

## Resources and APIs to verify before a first deployment

| Item | Why | Where |
|---|---|---|
| `aws_s3vectors_*`, `aws_bedrockagentcore_*`, `google_model_armor_template` | recent Terraform resource types; attribute names written from provider docs, not validated here | `*/terraform/extensions/` (CI validates them non-blocking) |
| Llama Stack `run.yaml` schema | the project changes fast (partly renamed OGX in 2026) | `providers/meta_llama/distribution/run.yaml` |
| Vertex Vector Search delete | `removeDatapoints` takes ids only; ids are remembered per process, else `<doc>#0..N` | `clouds/gcp/services.py` |
| Agent Engine `:query` and AgentCore payload contracts | defined by the agent code you deploy; this repo states the contract it expects | `clouds/*/…Runtime` docstrings |
| Model ids (`claude-opus-5-5`, `gpt-5`, `gemini-2.5-pro`, `meta.llama4-maverick…`) | availability differs per region and account | profiles (all overridable by env) |
| Azure OpenAI `reasoning`, `prompt_cache_key` on your deployment | supported per model/deployment | `providers/openai_azure/llm_service.py` |

## Platform gaps (what a profile cannot do)

* **Bedrock** has no Anthropic MCP connector, server-side refusal fallbacks, Managed Agents,
  web search or Batches API. Use Claude Platform on AWS when they are needed; the capability
  check refuses the combination at startup.
* **Anthropic has no embedding model.** Embeddings come from the cloud (Titan on AWS).
* **Llama Stack vector_io has no chunk delete**; documents are removed by re-indexing into a
  fresh store (blue/green). `delete()` raises instead of silently keeping stale chunks.
* **No managed prompt registry is bound for Azure or GCP** (none is stable enough to bind).
  Prompts stay in code or in E2A's `BasePromptRegistry` with your own store.
* **S3 Vectors is vector-only.** The keyword leg of E2A's hybrid contract is computed over
  an over-fetched candidate set (reciprocal rank fusion). OpenSearch Serverless would give
  native hybrid at higher cost.

## Design tensions

* **Double ownership.** Retries, quotas, caching and screening exist on both sides. The
  repository makes E2A the single owner of each (docs/ARCHITECTURE.md). Teams must keep
  E2A quotas below provider quotas, or they will see provider 429s instead of E2A's typed
  admission refusals.
* **Managed agent runtimes vs E2A's guarantees.** Solved by binding them as the model step
  with read-only tools. Workflows that need the runtime to *act* (place orders) are
  deliberately not supported.
* **Sampling parameters.** Current Claude and OpenAI reasoning models reject `temperature`.
  E2A's `temperature` key therefore only reaches Gemini and Llama.
* **Latency SLOs.** E2A's default `llm_max_latency` (3 s) suits a simulator, not a hosted
  model. Profiles raise it to 60 s; the request deadline still bounds every call.
* **Fifth vendor.** Mistral and Cohere were considered. Market use is far below the four
  covered, and Cohere already appears where enterprises use it (Bedrock Rerank). Adding one
  is a folder plus a profile file.
* **Java scope.** Only the model gateway (`LlmService`) is provided in Java, because that is
  what e2a-core in Java exposes and where the Spring services would call a model. RAG,
  guardrails and runtimes stay in the Python services, as in the landscape today.
