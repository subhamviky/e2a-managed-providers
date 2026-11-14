"""Managed guardrails as E2A governance stages.

The base is E2A's own since v1.6.1 (`e2a.ManagedGuardrailStage`): a managed classifier
(Bedrock Guardrails, Azure AI Content Safety Prompt Shields, Google Model Armor, Llama Guard
via Llama Stack shields) is one more IngressStage next to E2A's pattern FirewallStage, and
E2A owns the rules for every one of them:
  * the fields screened are `guardrail_fields`;
  * a block is FirewallRefusal, whichever service screened the request;
  * a service that cannot answer fails CLOSED unless `guardrail_fail_open`;
  * masked text replaces the field.
The same classifier screens responses with `e2a.ManagedGuardrailEgressStage(stage)`.

Each provider stage here implements only `_screen(text, ctx) -> Verdict` and its client.
"""
from e2a import GuardrailVerdict as Verdict
from e2a import ManagedGuardrailEgressStage, ManagedGuardrailStage

__all__ = ["ManagedGuardrailStage", "ManagedGuardrailEgressStage", "Verdict"]
