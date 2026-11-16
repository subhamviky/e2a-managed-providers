"""Every profile's model adapter meets E2A's model contract (e2a.port_contract.check_llm_service,
E2A v1.6.0): the canonical conversation, text, tool call, structured output, stream, refusal,
DLP, a throttle retried by E2A, the stop reason and truncation, each through the provider's
real SDK on a replayed wire."""
import pytest

from e2a.port_contract import check_llm_service
from e2a_managed.offline import offline_llm_service

PROFILES = ["anthropic_bedrock", "anthropic_claude_platform_aws", "openai_azure", "google_vertex", "ogx_aws"]


@pytest.mark.parametrize("profile", PROFILES)
def test_the_model_adapter_meets_the_e2a_contract(profile):
    assert len(check_llm_service(lambda responder: offline_llm_service(profile, responder)[0])) == 10
