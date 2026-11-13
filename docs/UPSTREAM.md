# What belongs upstream in E2A / A2C

You asked whether the wiring belongs in the frameworks. The test I used: code that knows
no provider and no project goes upstream; code that knows a provider stays here; code
that knows a project becomes an example runner.

| Piece | Knows a provider? | Knows a project? | Proposed home |
|---|---|---|---|
| `bindings.py`, `profile.py`, `capabilities.py` (profile → seams, capability check) | no | no | **E2A** (`e2a.bindings`): the composition root every E2A project needs |
| `config_keys.py` common keys (`provider_profile`, `llm_sdk_max_retries`, `reasoning_effort`, `fallback_model_id`, `mcp_servers`, `delegated_*`) | no | no | **E2A** registry |
| `codec.py` canonical conversation and tool-call shape | wire formats only | no | **E2A** (the canonical half); per-wire mappers stay here |
| `managed_llm.ManagedLLMService` (retry ownership, typed refusal, per-call params) | no | no | **E2A** `BaseLLMAgentService` hooks |
| `ManagedReranker` port | no | no | **E2A** `rag` (there is no reranker port today) |
| `stores.ConditionalIdempotencyStore` / `ConditionalCheckpointStore` | no | no | **E2A** `ports` (one state machine over `_read` / `_write_if`) |
| `delegated.DelegatedRuntime` / `DelegatedAgent` | no | no | **E2A** `agents` |
| `guardrails.ManagedGuardrailStage` | no | no | **E2A** `stages` |
| `offline.py`, `swap.py`, `pytest_plugin.py` | wire fixtures | no | **E2A** testing extra (`e2a[testing]`) |
| `providers/*`, `clouds/*` | yes | no | here (installable per profile: `pip install e2a-managed-providers[openai-azure]`) |
| A2C: `services/llm.py` template emits `bindings_from_profile().llm_service` instead of `SimulatedLLMService()`; request files gain `provider_profile` | – | – | **A2C** generator template + request schema |
| `examples/run_landscape.py`, `run_project_suites.sh` | – | yes | here (project-specific) |

None of this needed a framework change to work: the current E2A v1.3.0 contracts
were enough. One exception is `DelegatedAgent`, which overrides `_resolve_llm_service`.
E2A's conformance check accepts it, but an explicit per-agent `llm_service` hook upstream
would be cleaner.

The upstream changes are deliberately not pushed to `e2a-framework` or `a2c-framework`
from this work. They change shared contracts, so they should be a decision and a
versioned release of their own (E2A v1.4).
