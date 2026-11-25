"""GCP-01: the gemini_api profile, Gemini through the Developer API on the free tier."""
import asyncio
import datetime as dt
import json

import pytest

from e2a_managed.offline import offline_llm_service

SYSTEM = {"role": "system", "content": "You are the finance investigator."}
USER = {"role": "user", "content": "Why is line L-7 unmatched?"}


def run(coro):
    return asyncio.run(coro)


def test_calls_go_to_the_developer_api_with_a_key_and_no_vertex_only_fields():
    svc, rt = offline_llm_service("gemini_api", lambda m: "ok")
    out = run(svc.process_inference([SYSTEM, USER], tenant_id="acme", reasoning_effort="low"))
    req = rt.requests[-1]
    assert out["status"] == "SUCCESS" and out["text"] == "ok"
    assert req["url"].startswith("https://generativelanguage.googleapis.com/") and "aiplatform" not in req["url"]
    assert req["headers"].get("x-goog-api-key") == "offline-replay-key"
    assert "labels" not in json.dumps(req["body"]) and "cachedContent" not in json.dumps(req["body"])
    assert req["body"]["systemInstruction"]["parts"][0]["text"] == SYSTEM["content"]


def test_structured_output_is_requested_on_the_wire():
    schema = {"type": "object", "properties": {"cause": {"type": "string"}}, "required": ["cause"]}
    svc, rt = offline_llm_service("gemini_api", lambda m: json.dumps({"cause": "amount differs by 0.10"}))
    out = run(svc.process_inference([SYSTEM, USER], tenant_id="acme", response_schema=schema))
    assert out["structured_output"] == {"cause": "amount differs by 0.10"}
    gen = rt.requests[-1]["body"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"


def test_the_daily_free_tier_budget_stops_calls_before_they_leave_the_process():
    svc, rt = offline_llm_service("gemini_api", lambda m: "ok", gemini_max_requests_per_day=2, llm_retries=0)
    for _ in range(2):
        assert run(svc.process_inference([SYSTEM, USER], tenant_id="acme"))["status"] == "SUCCESS"
    third = run(svc.process_inference([SYSTEM, USER], tenant_id="acme"))
    assert third["status"] != "SUCCESS" and len(rt.requests) == 2            # the third never left
    assert "daily Gemini request budget" in json.dumps(third)


def test_the_budget_resets_each_utc_day():
    from e2a_managed.providers.google_gemini_api.llm_service import DailyQuotaExhausted, _DailyBudget
    budget, day = _DailyBudget(), dt.date(2026, 11, 24)
    budget.take(1, day)
    with pytest.raises(DailyQuotaExhausted) as error:
        budget.take(1, day)
    assert error.value.status_code == 429                                    # E2A classifies it as throttled
    budget.take(1, day + dt.timedelta(days=1))


def test_without_a_key_the_client_is_refused_with_a_clear_message(monkeypatch):
    from e2a_managed.providers.google_gemini_api.llm_service import gemini_api_client
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        gemini_api_client({})


def test_the_profile_extends_the_gcp_cloud_profile_and_binds_no_paid_ai_service(monkeypatch):
    from e2a_managed.bindings import bindings_from_profile
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "placeholder-project")
    profile = bindings_from_profile("gemini_api", verify=False).profile
    assert profile.bindings["idempotency_store"].endswith("FirestoreIdempotencyStore")
    assert profile.bindings["message_broker"].endswith("PubSubBroker")
    paid = ("vector_index", "reranker", "guardrail_stage", "delegated_runtime", "mcp_gateway_tool")
    assert not any(k in profile.bindings for k in paid)
    assert int(profile.config["max_rpm"]) <= 15 and int(profile.config["gemini_max_requests_per_day"]) > 0
