# What belongs upstream in E2A / A2C

You asked whether the wiring belongs in the frameworks. The test I used: code that knows
no provider and no project goes upstream; code that knows a provider stays here; code
that knows a project becomes an example runner.

| Piece | Knows a provider? | Knows a project? | Proposed home |
|---|---|---|---|
| `bindings.py`, `profile.py`, `capabilities.py` (profile → seams, capability check) | no | no | **Done upstream (E2A v1.6.0):** `e2a.bindings`, `e2a.capabilities`; these modules now re-export them |
| `config_keys.py` common keys | no | no | **Done upstream:** `provider_profile` (E2A v1.6.0), `reasoning_effort`, `guardrail_fields`, `delegated_max_turns` (v1.6.1) are E2A's and no longer registered here; `fallback_model_id` stays as a convenience that becomes an E2A `model_routes` entry |
| `codec.py` canonical conversation and tool-call shape | wire formats only | no | **Canonical half done upstream (E2A v1.6.0):** `e2a.llm_types`; the per-wire mappers stay here |
| `managed_llm.ManagedLLMService` (retry ownership, typed refusal, per-call params) | no | no | **Done upstream:** retries by error class (E2A v1.5.1), `ModelRefusal` and status `REFUSED` (v1.6.0). The provider-hook shape stays here |
| `ManagedReranker` port | no | no | **Done upstream (E2A v1.6.1):** `e2a.BaseReranker`; `ManagedReranker` is its client-holding base here |
| `stores` (claims and checkpoints) | – | – | **Done upstream:** `e2a-adapters` (E2A v1.5.0) implements the state machine once (`e2a_adapters.cas`) and for DynamoDB; this package now only binds those adapters from config |
| `delegated.DelegatedRuntime` / `DelegatedAgent` | no | no | **Done upstream (E2A v1.6.1):** `e2a.DelegatedRuntime`; `DelegatedAgent` is removed, a runtime is bound per agent with an E2A scope |
| `guardrails.ManagedGuardrailStage` | no | no | **Done upstream (E2A v1.6.1):** `e2a.ManagedGuardrailStage` (and an egress variant) |
| `offline.py`, `swap.py`, `pytest_plugin.py` | wire fixtures | no | here: they know each provider's wire. E2A's model contract (`check_llm_service`, v1.6.0) drives them |
| `providers/*`, `clouds/*` | yes | no | here (installable per profile: `pip install e2a-managed-providers[openai-azure]`) |
| A2C: `services/llm.py` template emits `bindings_from_profile().llm_service` instead of `SimulatedLLMService()`; request files gain `provider_profile` | – | – | **A2C** generator template + request schema |
| `examples/run_landscape.py`, `run_project_suites.sh` | – | yes | here (project-specific) |

**Where this stands.** The assessment of this proposal (hub, design note MP-00) decided what
goes upstream. E2A v1.5.1, v1.6.0 and v1.6.1 shipped the vendor-neutral parts listed above. This
repository stays separate on purpose: it changes at the pace of the providers' SDKs and wire
formats, not of E2A's contracts, so it is released on its own and pins an E2A range.

The last exception is gone: `DelegatedAgent`, which overrode the protected seam
`_resolve_llm_service`, is replaced by E2A's per-agent scopes (v1.6.1).
