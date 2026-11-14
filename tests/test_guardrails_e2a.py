"""The provider guardrails run on E2A's ManagedGuardrailStage (v1.6.1): E2A's rules, the
provider's classifier."""
from types import SimpleNamespace

import pytest
from e2a import FirewallRefusal, ManagedGuardrailEgressStage
from e2a.context import MessageLog

from e2a_managed.guardrails import ManagedGuardrailStage
from e2a_managed.providers.meta_llama.llm_service import LlamaGuardStage


class Safety:
    def __init__(self, fail=False):
        self.fail = fail

    def run_shield(self, shield_id, messages):
        if self.fail:
            raise ConnectionError("shield down")
        text = messages[0]["content"]
        if "attack" in text:
            return {"violation": {"violation_level": "error", "metadata": {"violation_type": "S14"}}}
        return {"violation": None}


def stage(config=None, fail=False):
    return LlamaGuardStage(config or {}, client=SimpleNamespace(safety=Safety(fail)))


def test_the_provider_stage_is_e2as_base_with_e2as_rules():
    assert issubclass(LlamaGuardStage, ManagedGuardrailStage)
    log = MessageLog([], "c1", "test")
    assert stage().check({"query": "where is my order"}, None, log) == {"query": "where is my order"}
    with pytest.raises(FirewallRefusal, match="S14"):
        stage().check({"query": "prompt attack"}, None, log)
    with pytest.raises(FirewallRefusal, match="unavailable"):
        stage(fail=True).check({"query": "hi"}, None, log)
    assert stage({"guardrail_fail_open": True}, fail=True).check({"query": "hi"}, None, log) == {"query": "hi"}
    assert stage({"guardrail_fields": ["message"]}).check({"query": "attack", "message": "ok"}, None, log)
    with pytest.raises(FirewallRefusal):
        ManagedGuardrailEgressStage(stage()).redact({"text": "an attack"}, {}, None, log)
