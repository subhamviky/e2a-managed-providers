# What belongs upstream in E2A / A2C

You asked whether the wiring belongs in the frameworks. The test I used: code that knows
no provider and no project goes upstream; code that knows a provider stays here; code
that knows a project becomes an example runner.

| Piece | Knows a provider? | Knows a project? | Proposed home |
|---|---|---|---|
| `bindings.py`, `profile.py`, `capabilities.py` (profile → seams, capability check) | no | no | **Done upstream (E2A v1.6.0):** `e2a.bindings`, `e2a.capabilities`; these modules now re-export them |
| `config_keys.py` common keys (`provider_profile`, `llm_sdk_max_retries`, `reasoning_effort`, `fallback_model_id`, `mcp_servers`, `delegated_*`) | no | no | **E2A** registry |
| `codec.py` canonical conversation and tool-call shape | wire formats only | no | **Canonical half done upstream (E2A v1.6.0):** `e2a.llm_types`; the per-wire mappers stay here |
| `managed_llm.ManagedLLMService` (retry ownership, typed refusal, per-call params) | no | no | **Done upstream:** retries by error class (E2A v1.5.1), `ModelRefusal` and status `REFUSED` (v1.6.0). The provider-hook shape stays here |
| `ManagedReranker` port | no | no | **E2A** `rag` (there is no reranker port today) |
| `stores` (claims and checkpoints) | – | – | **Done upstream:** `e2a-adapters` (E2A v1.5.0) implements the state machine once (`e2a_adapters.cas`) and for DynamoDB; this package now only binds those adapters from config |
| `delegated.DelegatedRuntime` / `DelegatedAgent` | no | no | **E2A** `agents` |
| `guardrails.ManagedGuardrailStage` | no | no | **E2A** `stages` |
| `offline.py`, `swap.py`, `pytest_plugin.py` | wire fixtures | no | here: they know each provider's wire. E2A's model contract (`check_llm_service`, v1.6.0) drives them |
| `providers/*`, `clouds/*` | yes | no | here (installable per profile: `pip install e2a-managed-providers[openai-azure]`) |
| A2C: `services/llm.py` template emits `bindings_from_profile().llm_service` instead of `SimulatedLLMService()`; request files gain `provider_profile` | – | – | **A2C** generator template + request schema |
| `examples/run_landscape.py`, `run_project_suites.sh` | – | yes | here (project-specific) |

**Where this stands.** The assessment of this proposal (hub, design note MP-00) decided what
goes upstream. E2A v1.5.1 and v1.6.0 shipped the vendor-neutral parts listed above. This
repository stays separate on purpose: it changes at the pace of the providers' SDKs and wire
formats, not of E2A's contracts, so it is released on its own and pins an E2A range.

One exception remains: `DelegatedAgent` overrides `_resolve_llm_service`, a protected seam
that is not a declared hook. E2A's conformance check accepts it, but it may change;
per-agent configuration upstream (P3, planned for E2A v1.6.1) replaces it.
