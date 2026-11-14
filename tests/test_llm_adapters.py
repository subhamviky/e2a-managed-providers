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
        assert value                                  # gemini-2.5: thinking budget derived from "high"
        assert value.get("thinkingBudget", value.get("thinking_budget")) == 24576
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


@pytest.mark.parametrize("profile", ["anthropic_bedrock", "openai_azure", "google_vertex"])
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


async def test_a_provider_that_falls_back_server_side_gets_no_e2a_route():
    calls = []

    def refuse(messages):
        calls.append(1)
        return {"text": "no", "stop_reason": "refusal"}
    svc, rt = offline_llm_service("anthropic_claude_platform_aws", refuse, fallback_model_id="fallback-model")
    out = await svc.process_inference([SYSTEM, USER], tenant_id="acme")
    assert out["status"] == "REFUSED" and len(calls) == 1          # the server already tried its fallbacks
    assert rt.requests[0]["body"].get("fallbacks") == "default"
