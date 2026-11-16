"""Model lifecycle check (P-2): a configured model past its published retirement date fails the
release; one retiring soon is reported."""
import datetime as dt

import pytest

from e2a_managed.lifecycle import ModelRetired, check_models, check_profile
from e2a_managed.release import ManagedReleasePipeline

TODAY = dt.date(2026, 11, 16)


def test_the_retired_gemini_default_would_have_been_caught():
    found = {f["key"]: f for f in check_profile("google_vertex", {"model_id": "gemini-2.5-pro"}, today=TODAY)}
    f = found["model_id"]
    assert found["embedding_model_id"]["status"] == "OK"
    assert f["status"] == "RETIRED" and f["retires"] == "2026-10-20" and f["replacement"] == "gemini-3.8-flash"


def test_a_model_retiring_inside_the_window_is_a_warning():
    [f] = check_models("foundry", {"model_id": "Llama-4-Maverick-17B-128E-Instruct-FP8"}, TODAY, warn_days=90)
    assert f["status"] == "WARNING" and f["days_left"] == 3


def test_a_bedrock_inference_profile_id_names_the_same_model():
    [f] = check_models("bedrock", {"model_id": "us.meta.llama4-maverick-17b-instruct-v1:0"}, TODAY)
    assert f["status"] == "OK" and f["retires"] == "2027-03-30"
    [late] = check_models("bedrock", {"model_id": "us.meta.llama4-maverick-17b-instruct-v1:0"}, dt.date(2027, 3, 30))
    assert late["status"] == "RETIRED"


def test_an_unlisted_model_is_not_judged():
    assert check_models("bedrock", {"model_id": "anthropic.claude-opus-5-5"}, TODAY) == []
    assert check_models(None, {"model_id": "gemini-2.5-pro"}, TODAY) == []


def test_every_profile_default_is_current():
    from e2a_managed.profile import provider_profiles
    for name in provider_profiles():
        assert all(f["status"] != "RETIRED" for f in check_profile(name, today=TODAY)), name


def test_the_release_pipeline_refuses_a_retired_model_in_its_static_stage():
    pipeline = ManagedReleasePipeline(profile="google_vertex")
    with pytest.raises(ModelRetired, match="gemini-2.5-pro retired on agent_platform on 2026-10-20"):
        pipeline._run_static_security_scans({}, binding_overrides={"model_id": "gemini-2.5-pro"}, today=TODAY)
    assert pipeline._run_static_security_scans({}, today=TODAY) is True
    assert {f["model"] for f in pipeline.lifecycle_findings} == {"gemini-3.8-flash", "gemini-embedding-001"}
