"""IaC manifests, the release gate, the swap plugin, and E2A conformance of this package."""
import json
from pathlib import Path

import pytest
from e2a.conformance import check_paths

from e2a_managed import bindings_from_profile

from .conftest import PROFILES

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("profile", PROFILES)
def test_landing_zone_manifest_and_tfvars(profile, tmp_path):
    b = bindings_from_profile(profile)
    prov = type(b.infra_provisioner)(b.config(), out_dir=tmp_path)
    manifest = prov.provision_landing_zone("acme", "POOL", "HIGH", config=b.profile.config)
    assert manifest["status"] == "PROVISIONED", manifest.get("error_trace")
    assert set(manifest["topology_maps"]) >= {"networks", "iam_governance", "saga_orchestration", "managed_ai"}
    tfvars = json.loads((tmp_path / "acme.auto.tfvars.json").read_text())
    assert tfvars["tenant_id"] == "acme"
    assert Path(manifest["terraform"]["module"]).is_dir(), "the cloud's terraform module must exist"


@pytest.mark.parametrize("profile", PROFILES)
def test_release_gate_checks_the_profile_before_deploying(profile):
    b = bindings_from_profile(profile)
    pipeline = type(b.release_pipeline)({}, profile=profile)
    report = pipeline.run_pipeline({"faithfulness_threshold": 0.5}, skip_tests=True)
    assert report["status"] == "SUCCESS", report.get("error")
    assert report["deployment_metadata"]["profile"] == profile


def test_release_gate_refuses_literal_secrets():
    b = bindings_from_profile("openai_azure")
    report = type(b.release_pipeline)({"azure_openai_api_key": "sk-live-123"}, profile=None) \
        .run_pipeline({"faithfulness_threshold": 0.5}, skip_tests=True)
    assert report["status"] == "FAILED" and "literal secrets" in report["error"]


def test_swap_keeps_the_simulator_s_surface():
    from e2a_simulated_llm import SimulatedLLMService, system_contains

    from e2a_managed.swap import managed_replay
    sim = SimulatedLLMService().add_rule("r", system_contains("router"), lambda m, c: "routed")
    managed = managed_replay(sim, "google_vertex")
    assert managed.rule_names() == ["r"]                      # forwarded to the simulator
    assert type(managed).__mro__[1].__name__ == "VertexGeminiLLMService"


def test_this_package_conforms_to_e2a():
    findings = [f for f in check_paths([str(ROOT / "e2a_managed")]) if f.severity.lower() == "error"]
    assert not findings, "\n".join(map(str, findings))


def test_swap_builds_a_self_binding_factory_with_the_profile_unset(monkeypatch):
    """A project factory that reads E2A_PROVIDER_PROFILE itself (the GCP-01 services) must hand the swap its own
    simulated model, not the provider's real one; the profile is restored afterwards."""
    import sys
    import types
    from e2a_simulated_llm import SimulatedLLMService
    from e2a_managed import swap

    seen = {}

    def build_llm(profile=None):
        seen["profile_during"] = __import__("os").environ.get("E2A_PROVIDER_PROFILE")
        return SimulatedLLMService()

    module = types.ModuleType("fake_project_llm")
    module.build_llm = build_llm
    monkeypatch.setitem(sys.modules, "fake_project_llm", module)
    monkeypatch.setenv("E2A_PROVIDER_PROFILE", "gemini_api")
    monkeypatch.setattr(swap, "managed_replay", lambda sim, profile: ("wrapped", sim, profile))
    swap.wrap_factory("fake_project_llm:build_llm", "gemini_api")
    kind, sim, profile = module.build_llm()
    assert kind == "wrapped" and isinstance(sim, SimulatedLLMService) and profile == "gemini_api"
    assert seen["profile_during"] is None                                  # the project's factory saw no profile
    assert __import__("os").environ["E2A_PROVIDER_PROFILE"] == "gemini_api"   # and it is back afterwards
