"""The provider guardrails run on E2A's ManagedGuardrailStage (v1.6.1): E2A's rules, the
provider's classifier."""
from types import SimpleNamespace

import pytest
from e2a import FirewallRefusal, ManagedGuardrailEgressStage
from e2a.context import MessageLog

from e2a_managed.guardrails import ManagedGuardrailStage, Verdict


class Classifier(ManagedGuardrailStage):
    """The smallest provider stage: only _screen and a client, as every provider stage."""
    name = "test_classifier"

    def __init__(self, config=None, fail=False):
        super().__init__(config or {}, client=object())
        self.fail = fail

    def _build_client(self, config):
        return object()

    def _screen(self, text, ctx):
        if self.fail:
            raise ConnectionError("classifier down")
        return Verdict(False, ["S14"]) if "attack" in text else Verdict(True)


def stage(config=None, fail=False):
    return Classifier(config, fail)


def test_the_provider_stage_is_e2as_base_with_e2as_rules():
    assert issubclass(Classifier, ManagedGuardrailStage)
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
