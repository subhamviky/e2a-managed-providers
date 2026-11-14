"""Profiles, binding and the E2A resolution order (kwargs > profile config > env > default)."""
import pytest
from e2a import resolve_config

from e2a_managed import CapabilityError, ProviderProfile, available_profiles, bindings_from_profile
from e2a_managed.bindings import SEAMS

from .conftest import PROFILES


def test_every_profile_is_listed():
    # E2A's own registry lists them, next to E2A's 'simulated' profile and e2a-adapters' cloud profiles
    assert sorted(available_profiles()) == sorted(PROFILES + ["simulated", "aws", "gcp", "azure"])


@pytest.mark.parametrize("name", PROFILES)
def test_every_profile_binds_and_provides_its_features(name):
    b = bindings_from_profile(name)
    provided = b.verify()                       # builds every seam lazily; no cloud call is made
    assert "llm_service" in provided
    assert set(b.profile.bindings) <= set(SEAMS)
    cfg = b.config(tools={})
    assert cfg["llm_service"] is b.llm_service and cfg["observability_engine"] is b.observability_engine


def test_profile_is_selected_by_environment(monkeypatch):
    monkeypatch.setenv("E2A_PROVIDER_PROFILE", "google_vertex")
    assert bindings_from_profile().profile.provider == "google"


def test_placeholders_use_env_then_default_and_drop_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_ID", "anthropic.claude-sonnet-5-5")
    p = ProviderProfile.load("anthropic_bedrock")
    assert p.config["model_id"] == "anthropic.claude-sonnet-5-5"        # env
    assert p.config["llm_max_latency"] == 60                             # default, typed
    assert "s3_vector_bucket" not in p.config                            # unset -> E2A env/default applies


def test_resolution_order_is_e2a_s_own():
    b = bindings_from_profile("anthropic_bedrock")
    cfg = b.config()
    assert resolve_config("reasoning_effort", cfg) == "medium"                            # profile
    assert resolve_config("reasoning_effort", cfg, {"reasoning_effort": "high"}) == "high"  # call wins


def test_a_feature_the_platform_lacks_fails_at_startup():
    b = bindings_from_profile("anthropic_bedrock")
    with pytest.raises(CapabilityError, match="mcp_connector"):
        b.verify(["tool_use", "mcp_connector"])          # Bedrock has no Anthropic MCP connector
    bindings_from_profile("anthropic_claude_platform_aws").verify(["tool_use", "mcp_connector"])


def test_overrides_replace_a_seam_and_win_over_the_profile():
    sentinel = object()
    b = bindings_from_profile("openai_azure", overrides={"vector_index": sentinel, "model_id": "gpt-5-mini"})
    assert b.vector_index is sentinel and b.config()["model_id"] == "gpt-5-mini"


def test_unknown_profile_names_the_available_ones():
    with pytest.raises(FileNotFoundError, match="anthropic_bedrock"):
        ProviderProfile.load("nope")
