"""Managed agent runtimes, kept inside the E2A agent lifecycle.

The base is E2A's own since v1.6.1 (`e2a.DelegatedRuntime`): a managed runtime (Claude Managed
Agents, Bedrock AgentCore Runtime, the OpenAI Responses API with hosted tools, Vertex AI Agent
Engine, Llama Stack's Responses API) is the model step of one agent, never a replacement for it:

    BaseAgent.run():  screen -> _prepare -> decide -> _apply_policy -> _build_messages
                      -> infer  == DelegatedRuntime: one managed session      <- delegated
                      -> _evaluate_output -> _act (io_modify) -> _confirm -> redaction

E2A enforces, for every runtime: read tools only (MaterialToolRefused), host-side tool calls
through the tool's own execute() for the request's tenant (`run_tool`), and a session bounded
by `delegated_max_turns` (DelegatedTurnLimit) and by the request deadline.

Bind a runtime to one agent with an E2A scope; the rest of the workflow keeps its own
llm_service (this replaces the earlier DelegatedAgent, which overrode a protected seam):

    config:
      scopes: {"agent:research": {"llm_service": "@delegated_runtime"}}

Each runtime here implements `_run_session(messages, config, tenant_id, session, kwargs)` and
its client.
"""
from e2a import DelegatedRuntime, DelegatedTurnLimit, MaterialToolRefused

__all__ = ["DelegatedRuntime", "DelegatedTurnLimit", "MaterialToolRefused"]
