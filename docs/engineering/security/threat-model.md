# Threat model: e2a-managed-providers

- **Status:** Draft 2026-11-28, awaiting owner review · STRIDE for a provider layer: the risks are credentials, data sent to providers, and model behaviour.

| # | Threat | Control | Evidence |
|---|---|---|---|
| M1 | **Information disclosure**: provider API keys | credentials come from the environment or a secret store through E2A's credential providers; none is committed; offline tests use replay | `e2a_managed`, tests |
| M2 | **Information disclosure**: personal data sent to a provider | the consumer enables E2A's DLP and redaction; a provider's terms may allow use of the data sent, so only synthetic data may be sent | data handling page |
| M3 | **Tampering**: a model reply that is not what was asked | E2A's evaluation and fallbacks; this layer only transports | E2A |
| M4 | **Denial of service**: quota exhaustion | retries are bounded; consumers degrade to deterministic fallbacks; live runs stop on a 429 | consuming projects |
| M5 | **Supply chain**: provider SDK vulnerabilities and retired models | SDKs pinned; model lifecycle test fails past a retirement date | CI |
| M6 | **Misuse of a profile**: binding a feature the provider lacks | CI step 'every profile binds and provides its features' | CI |

Residual: dependency and supply-chain risk is managed by pinning tags and running CI, not by signing or an SBOM policy; a consuming project must run the conformance kit to benefit from the safety order; there is no penetration test.
