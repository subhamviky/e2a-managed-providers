"""Every model adapter through its provider's REAL SDK, offline (wire replay)."""
import json

import pytest

from e2a_managed.managed_llm import ModelRefusal
from e2a_managed.offline import offline_llm_service

from .conftest import PROFILES, SYSTEM, USER

TOOLS = [{"name": "get_order", "description": "One order", "parameters": {
    "type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}]


@pytest.mark.parametrize("profile", PROFILES)
async def test_unary_and_stream(profile):
    svc, rt = offline_llm_service(profile, lambda m: f"answer to: {m[-1]['content']}")
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["status"] == "SUCCESS" and out["text"] == "answer to: What is our refund policy?"
    assert out["tokens_used"] > 0
    chunks = [c async for c in svc.process_inference_stream([SYSTEM, USER], tenant_id="acme")]
    assert "".join(chunks) == "answer to: What is our refund policy?"
    assert len(rt.requests) == 2


@pytest.mark.parametrize("profile", PROFILES)
async def test_tool_call_round_trip(profile):
    def responder(messages):
        if messages[-1]["role"] == "tool":
            return f"Order status: {messages[-1]['content']}"
        return {"text": "", "tool_calls": [{"id": "call_1", "name": "get_order", "arguments": {"order_id": "ORD-1"}}]}
    svc, rt = offline_llm_service(profile, responder)
    first = await svc.process_inference([SYSTEM, USER], tenant_id="acme", tools=TOOLS)
    assert first["action"] == "TOOL_CALL_DETECTED"
    call = first["tool_calls"][0]
    assert call["name"] == "get_order" and call["arguments"] == {"order_id": "ORD-1"}
    history = [SYSTEM, USER, {"role": "assistant", "content": "", "tool_calls": first["tool_calls"]},
               {"role": "tool", "tool_call_id": call["id"], "content": "SHIPPED"}]
    second = await svc.process_inference(history, tenant_id="acme", tools=TOOLS)
    assert second["text"] == "Order status: SHIPPED"


@pytest.mark.parametrize("profile,path,expected", [
    ("anthropic_bedrock", ("output_config", "effort"), "high"),
    ("openai_azure", ("reasoning", "effort"), "high"),
    ("google_vertex", ("generationConfig", "thinkingConfig"), None),
])
async def test_vendor_parameter_passed_per_call_wins_over_the_profile(profile, path, expected):
    svc, rt = offline_llm_service(profile, lambda m: "ok")
    await svc.process_inference([SYSTEM, USER], tenant_id="acme", reasoning_effort="high")
    body = rt.requests[-1]["body"]
    value = body.get(path[0], {}).get(path[1])
    if expected is None:
        assert value                                  # gemini-3.8-flash: a thinking level, not a budget
        assert value.get("thinkingLevel", value.get("thinking_level")) == "HIGH"
    else:
        assert value == expected


async def test_claude_never_sends_sampling_parameters_and_caches_the_static_prefix():
    svc, rt = offline_llm_service("anthropic_bedrock", lambda m: "ok", min_cache_threshold=1)
    await svc.process_inference([{"role": "system", "content": "policy " * 50}, USER], tenant_id="acme")
    body = rt.requests[-1]["body"]
    assert "temperature" not in body
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["model"] == "anthropic.claude-opus-5-5"


async def test_structured_output_is_requested_on_the_wire():
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"]}
    svc, rt = offline_llm_service("openai_azure", lambda m: json.dumps({"intent": "X"}))
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme", response_schema=schema)
    assert out["structured_output"] == {"intent": "X"}
    assert rt.requests[-1]["body"]["text"]["format"]["schema"] == schema


async def test_mcp_connector_only_where_the_platform_has_it():
    servers = [{"name": "o2c-tools", "url": "https://o2c.example.com/mcp"}]
    platform, rt = offline_llm_service("anthropic_claude_platform_aws", lambda m: "ok", mcp_servers=servers)
    await platform.process_inference([SYSTEM, USER], tenant_id="acme")
    body = rt.requests[-1]["body"]
    assert body["mcp_servers"][0]["name"] == "o2c-tools"
    assert {"type": "mcp_toolset", "mcp_server_name": "o2c-tools"} in body["tools"]
    bedrock, rt2 = offline_llm_service("anthropic_bedrock", lambda m: "ok", mcp_servers=servers)
    await bedrock.process_inference([SYSTEM, USER], tenant_id="acme")
    assert "mcp_servers" not in rt2.requests[-1]["body"]


@pytest.mark.parametrize("profile", ["anthropic_bedrock", "anthropic_claude_platform_aws", "openai_azure",
                                     "google_vertex"])
async def test_refusal_is_typed_and_routed_by_e2a_to_the_fallback_model(profile):
    def refuse(messages):
        return {"text": "I can't help with that.", "stop_reason": "refusal"}
    svc, rt = offline_llm_service(profile, refuse, fallback_model_id=None)
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    # E2A v1.6.0: a refusal is a typed policy outcome (REFUSED), not a failure
    assert out["status"] == "REFUSED" and out["refusal"]["detail"] == "I can't help with that."

    calls = []

    def once(messages):
        calls.append(1)
        return {"text": "no", "stop_reason": "refusal"} if len(calls) == 1 else "answered by fallback"
    svc2, rt2 = offline_llm_service(profile, once, fallback_model_id="fallback-model")
    out2 = await svc2.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out2["text"] == "answered by fallback" and len(calls) == 2
    # E2A v1.6.1: fallback_model_id became a model route; E2A records where the answer came from
    assert out2["fallback_from"]["reason"] == "refusal"
    assert "fallback-model" in json.dumps(rt2.requests[-1]["body"]) or "fallback-model" in rt2.requests[-1]["url"]


@pytest.mark.parametrize("profile", PROFILES)
async def test_e2a_owns_dlp_and_retries(profile):
    svc, rt = offline_llm_service(profile, lambda m: "Customer SSN is 123-45-6789.")
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert "123-45-6789" not in out["text"] and out["dlp_redactions"] == 1
    # E2A (v1.5.1+) retries model calls by error class, so the SDKs do not: one retry authority.
    assert svc._sdk_options({}, {})["max_retries"] == 0


@pytest.mark.parametrize("profile", PROFILES)
async def test_each_adapter_declares_its_features_and_e2a_drops_what_it_cannot_take(profile):
    svc, rt = offline_llm_service(profile, lambda m: '{"ok": true}')
    features = svc._features_for({})
    assert features.get("structured_output") == "native"
    log = []
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme", reasoning_effort="high", message_log=log,
                                      response_schema={"type": "object", "required": ["ok"]})
    assert out["status"] == "SUCCESS" and out["structured_output_valid"] is True
    dropped = [e.get("param") for e in log if e.get("event") == "param_dropped"]
    assert ("reasoning_effort" in dropped) == (features.get("reasoning") == "none")


def test_server_side_fallback_is_sent_only_by_a_class_that_declares_it():
    # Claude Platform on AWS does not offer server-side fallback: no `fallbacks`, no beta header
    from e2a_managed.capabilities import Capability as C
    from e2a_managed.providers.anthropic_aws.llm_service import FALLBACK_BETA, ClaudePlatformAWSLLMService
    platform = ClaudePlatformAWSLLMService.__new__(ClaudePlatformAWSLLMService)
    req = platform._build_request([SYSTEM, USER], "m", None, 64, None, None, None, {}, {})
    assert "fallbacks" not in req and FALLBACK_BETA not in req.get("betas", [])
    assert C.REFUSAL_FALLBACK_SERVER not in ClaudePlatformAWSLLMService.capabilities

    class WithServerFallback(ClaudePlatformAWSLLMService):      # a platform that offers it
        server_side_fallbacks = True
    other = WithServerFallback.__new__(WithServerFallback)
    req = other._build_request([SYSTEM, USER], "m", None, 64, None, None, None, {}, {})
    assert req["fallbacks"] == "default" and FALLBACK_BETA in req["betas"]


@pytest.mark.parametrize("model,effort,level", [
    ("gemini-3.8-flash", "max", "HIGH"),           # no XHIGH on this model: its highest instead
    ("gemini-3.8-flash", "xhigh", "HIGH"),
    ("gemini-3.8-flash", "minimal", "LOW"),        # no MINIMAL: its lowest
    ("gemini-4-carbon", "medium", "MEDIUM"),
    ("gemini-4-hydrogen", "max", "XHIGH"),
    ("gemini-3.6-flash", "minimal", "MINIMAL"),
    ("gemini-3-pro-preview", "medium", "LOW"),     # low or high only: lowered, never raised
    ("gemini-future-model", "high", "HIGH"),       # unknown model: low, medium, high
])
def test_gemini_thinking_level_is_one_the_model_accepts(model, effort, level):
    from e2a_managed.providers.google_vertex.llm_service import thinking_level
    assert thinking_level(model, effort) == level


async def test_gemini_2_5_still_gets_a_thinking_budget():
    svc, rt = offline_llm_service("google_vertex", lambda m: "ok", model_id="gemini-2.5-flash")
    await svc.process_inference([SYSTEM, USER], tenant_id="acme", reasoning_effort="high")
    cfg = rt.requests[-1]["body"]["generationConfig"]["thinkingConfig"]
    assert cfg.get("thinkingBudget", cfg.get("thinking_budget")) == 24576


# ------------------------------------------------------------------ provider error classes (P-2)
@pytest.mark.parametrize("code", ["credit_balance_exhausted", "organization_spend_limit_exceeded",
                                  "project_spend_limit_exceeded", "organization_usage_limit_exceeded"])
async def test_an_openai_account_limit_fails_at_once_and_is_not_retried(code):
    svc, rt = offline_llm_service("openai_azure", lambda m: {"error_status": 429, "error_code": code})
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["status"] == "FAILED" and out["error_kind"] == "deterministic"
    assert len(rt.requests) == 1                     # waiting would not clear it: no retry


async def test_an_openai_insufficient_quota_type_is_an_account_limit_too():
    svc, rt = offline_llm_service("openai_azure", lambda m: {"error_status": 429, "error_code": "billing_hard_limit",
                                                            "error_type": "insufficient_quota"})
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["error_kind"] == "deterministic" and len(rt.requests) == 1


async def test_an_ordinary_openai_rate_limit_is_still_retried():
    calls = []

    def responder(messages):
        calls.append(1)
        return {"error_status": 429, "error_code": "rate_limit_exceeded"} if len(calls) == 1 else "ok"
    svc, rt = offline_llm_service("openai_azure", responder)
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["text"] == "ok" and len(rt.requests) == 2


async def test_a_foundry_content_filter_block_is_refused_not_failed():
    svc, rt = offline_llm_service("openai_azure", lambda m: {"error_status": 400, "error_code": "content_filter"})
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["status"] == "REFUSED" and out["refusal"]["category"] == "content_filter"
    assert len(rt.requests) == 1


async def test_a_foundry_content_filter_block_routes_to_the_fallback_deployment():
    calls = []

    def responder(messages):
        calls.append(1)
        return {"error_status": 400, "error_code": "content_filter"} if len(calls) == 1 else "answered by fallback"
    svc, rt = offline_llm_service("openai_azure", responder, fallback_model_id="gpt-fallback")
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["text"] == "answered by fallback" and out["fallback_from"]["reason"] == "refusal"


def test_a_foundry_output_block_in_content_filters_is_a_refusal():
    from e2a_managed.providers.openai_azure.llm_service import AzureOpenAIResponsesLLMService
    svc = AzureOpenAIResponsesLLMService({}, client=object())
    response = {"id": "r1", "model": "gpt", "status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ""}]}],
        "usage": {"input_tokens": 1, "output_tokens": 0},
        "content_filters": [{"blocked": True, "source_type": "completion", "content_filter_results": {}}]}
    out = svc._parse(response)
    assert out["metadata"]["stop_reason"] == "refusal" and out["metadata"]["refusal_category"] == "content_filter"
    clean = dict(response, content_filters=[{"blocked": False}])
    assert svc._parse(clean)["metadata"]["stop_reason"] != "refusal"
