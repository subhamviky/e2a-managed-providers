"""Managed agent runtimes stay inside the E2A agent lifecycle and may only read."""
import json
from types import SimpleNamespace

import pytest
from e2a import BaseAgent, BaseToolService

from e2a_managed.delegated import MaterialToolRefused
from e2a_managed.offline import ReplayTransport, offline_client
from e2a_managed.providers.anthropic_aws.delegated_runtime import ClaudeManagedAgentsRuntime
from e2a_managed.providers.openai_azure.delegated_runtime import ResponsesAgentRuntime

SCHEMA = {"description": "One order", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}}}


class ReadOrder(BaseToolService):
    material = False

    def __init__(self):
        self.calls = []

    def _build_payload(self, payload, config=None, **kwargs):
        return payload

    def _validate_input(self, payload, config=None, **kwargs):
        return "order_id" in payload

    def _get_endpoint(self, config=None, tenant_id=None, **kwargs):
        return "mem://orders"

    async def _send_request(self, endpoint, body, token=None, timeout=None, config=None, **kwargs):
        self.calls.append((kwargs.get("tenant_id"), body))
        return {"status": 200, "physical_success": True, "order": {"id": body["order_id"], "state": "SHIPPED"}}


class PlaceOrder(ReadOrder):
    material = True


def test_a_material_tool_can_never_be_given_to_a_runtime():
    with pytest.raises(MaterialToolRefused):
        ResponsesAgentRuntime({}, tools={"place_order": (PlaceOrder(), SCHEMA)})


async def test_responses_runtime_answers_function_calls_host_side_with_the_tenant():
    def responder(messages):
        if messages[-1]["role"] == "tool":
            state = json.loads(messages[-1]["content"])["order"]["state"]
            return f"ORD-7 is {state}."
        return {"text": "", "tool_calls": [{"id": "c1", "name": "get_order", "arguments": {"order_id": "ORD-7"}}]}
    rt = ReplayTransport("openai_responses", responder)
    tool = ReadOrder()
    runtime = ResponsesAgentRuntime({"delegated_runtime_id": "gpt-5", "delegated_max_turns": 3},
                                    client=offline_client("openai", rt), tools={"get_order": (tool, SCHEMA)})
    out = await runtime.process_inference([{"role": "system", "content": "You help."},
                                           {"role": "user", "content": "Where is ORD-7?"}], tenant_id="acme")
    assert out["text"] == "ORD-7 is SHIPPED."
    assert tool.calls == [("acme", {"order_id": "ORD-7"})]
    assert rt.requests[0]["body"]["tools"][0]["name"] == "get_order" and rt.requests[0]["body"]["store"] is False


async def test_runtime_tool_budget_is_enforced():
    rt = ReplayTransport("openai_responses", lambda m: {"text": "", "tool_calls": [
        {"id": f"c{len(m)}", "name": "get_order", "arguments": {"order_id": "X"}}]})
    runtime = ResponsesAgentRuntime({"delegated_runtime_id": "gpt-5", "delegated_max_turns": 2},
                                    client=offline_client("openai", rt), tools={"get_order": (ReadOrder(), SCHEMA)})
    out = await runtime.process_inference([{"role": "user", "content": "loop"}], tenant_id="acme")
    assert out["status"] == "FAILED" and out["error_type"] == "DelegatedTurnLimit"


class FakeSessions:
    """Duck-typed async Managed Agents session API: emits a custom tool call, then the answer."""

    def __init__(self):
        self.sent, self.created = [], []
        self.events = SimpleNamespace(stream=self._stream, send=self._send)

    async def create(self, **kw):
        self.created.append(kw)
        return SimpleNamespace(id="sesn_1")

    async def _send(self, session_id, events):
        self.sent.extend(events)

    def _stream(self, session_id):
        sessions = self

        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                async def gen():
                    yield SimpleNamespace(type="agent.custom_tool_use", id="sevt_1", name="get_order",
                                          input={"order_id": "ORD-9"})
                    yield SimpleNamespace(type="session.status_idle",
                                          stop_reason=SimpleNamespace(type="requires_action"))
                    result = json.loads(sessions.sent[-1]["content"][0]["text"])
                    yield SimpleNamespace(type="agent.message",
                                          content=[SimpleNamespace(type="text", text=f"ORD-9: {result['order']['state']}")])
                    yield SimpleNamespace(type="span.model_request_end",
                                          model_usage=SimpleNamespace(input_tokens=20, output_tokens=4))
                    yield SimpleNamespace(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn"))
                return gen()
        return Stream()


async def test_claude_managed_agents_runtime_round_trip():
    sessions = FakeSessions()
    client = SimpleNamespace(beta=SimpleNamespace(sessions=sessions))
    tool = ReadOrder()
    runtime = ClaudeManagedAgentsRuntime({"delegated_runtime_id": "agent_01", "delegated_runtime_environment": "env_01"},
                                         client=client, tools={"get_order": (tool, SCHEMA)})
    out = await runtime.process_inference([{"role": "system", "content": "ctx"},
                                           {"role": "user", "content": "ORD-9?"}], tenant_id="acme")
    assert out["text"] == "ORD-9: SHIPPED" and out["tokens_used"] == 24
    assert sessions.created[0]["agent"] == "agent_01" and sessions.created[0]["metadata"]["tenant_id"] == "acme"
    assert sessions.sent[0]["type"] == "user.message"
    assert sessions.sent[1]["type"] == "user.custom_tool_result" and sessions.sent[1]["custom_tool_use_id"] == "sevt_1"
    assert tool.calls == [("acme", {"order_id": "ORD-9"})]


def test_a_scope_binds_the_runtime_to_one_agent_and_the_full_lifecycle_runs():
    """E2A v1.6.1 scopes replace DelegatedAgent: the runtime is this agent's llm_service only."""
    class Advisor(BaseAgent):
        agent_name = "Advisor"

        def _build_messages(self, state, ctx):
            return [{"role": "system", "content": "advisor"}, {"role": "user", "content": state["query"]}]

        def _evaluate_output(self, response, state, ctx):
            return 1.0 if "SHIPPED" in response.get("text", "") else 0.0

        def _fallback(self, state, ctx):
            return {"text": "fallback"}

        def _handle_error(self, error, state, ctx):
            state["error"] = str(error)

    def responder(messages):
        if messages[-1]["role"] == "tool":
            return "It is SHIPPED."
        return {"text": "", "tool_calls": [{"id": "c1", "name": "get_order", "arguments": {"order_id": "ORD-1"}}]}
    rt = ReplayTransport("openai_responses", responder)
    runtime = ResponsesAgentRuntime({"delegated_runtime_id": "gpt-5"}, client=offline_client("openai", rt),
                                    tools={"get_order": (ReadOrder(), SCHEMA)})
    config = {"llm_service": object(), "scopes": {"agent:Advisor": {"llm_service": runtime}}}
    state = Advisor().run({"query": "Where is ORD-1?"}, config, tenant_id="acme")
    assert state["evaluation"] == "ACCEPTED" and state["response"]["text"] == "It is SHIPPED."
