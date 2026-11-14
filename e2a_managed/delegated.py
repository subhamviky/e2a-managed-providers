"""Managed agent runtimes, kept inside the E2A agent lifecycle.

Claude Managed Agents, Bedrock AgentCore Runtime, the OpenAI Responses API with
hosted tools, Vertex AI Agent Engine and Llama Stack's Responses API all run the
reasoning loop on the provider's side. E2A's guarantees (screening before anything
reads the request, the agent's own policy, idempotency, four-eyes approval, material
effects only through io_modify with an idempotency key, confirmation against the
system of record) must not move there.

So a managed runtime is bound as the *model step* of one agent, not as a replacement
for the agent:

    BaseAgent.run():  screen -> _prepare -> decide -> _apply_policy -> _build_messages
                      -> infer  == DelegatedRuntime: one managed session/run   <- delegated
                      -> _evaluate_output -> _act (io_modify) -> _confirm -> redaction

Rules enforced here, for every provider:
  * the runtime may call READ tools only. A tool whose `material` is True is never
    offered to it; a call naming one is refused and reported back as an error result.
  * host-side tool calls run through the tool's own execute(), scoped to the request's
    tenant, so typed outcomes, the circuit breaker and tenant isolation still apply.
  * the run is bounded by `delegated_max_turns` and by E2A's request deadline (the base
    class runs the session under its upstream timeout and quota like any model call).
"""
import abc
import json
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from e2a import BaseAgent, BaseLLMAgentService, BaseToolService

from . import codec, config_keys  # noqa: F401
from .capabilities import Capability

ToolSpec = Tuple[BaseToolService, Dict[str, Any]]      # (tool, {"description", "parameters"})


class MaterialToolRefused(PermissionError):
    pass


class DelegatedRuntime(BaseLLMAgentService):
    provider = "abstract"
    capabilities: FrozenSet[str] = frozenset({Capability.MANAGED_AGENT_RUNTIME})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None,
                 tools: Optional[Dict[str, ToolSpec]] = None, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client = client
        self.tools: Dict[str, ToolSpec] = {}
        for name, spec in (tools or {}).items():
            self.bind_tool(name, *spec)

    def bind_tool(self, name: str, tool: BaseToolService, schema: Dict[str, Any]) -> None:
        if getattr(tool, "material", False):
            raise MaterialToolRefused(f"{name} has a material effect; a delegated runtime may only read")
        self.tools[name] = (tool, schema)

    def tool_declarations(self) -> List[Dict[str, Any]]:
        return [{"name": n, "description": s.get("description", ""), "parameters": s["parameters"]}
                for n, (_, s) in self.tools.items()]

    async def _run_tool(self, name: str, arguments: Dict[str, Any], tenant_id: str,
                        config: Dict[str, Any]) -> str:
        """Execute one host-side read tool for the runtime; the result text goes back to it."""
        if name not in self.tools:
            return json.dumps({"error": f"tool '{name}' is not available to this agent"})
        tool, _ = self.tools[name]
        if getattr(tool, "material", False):
            return json.dumps({"error": f"tool '{name}' has a material effect and is refused"})
        try:
            out = await tool.execute(dict(arguments), config, tenant_id=tenant_id, safe_to_retry=True)
        except Exception as e:                        # report, do not crash the managed run
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return json.dumps(codec.as_dict(out), default=str)

    def _client_for(self, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Any:
        if self._client is None:
            self._client = self._build_client({**self._config, **(config or {})}, kwargs)
        return self._client

    @abc.abstractmethod
    def _build_client(self, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Any:
        """Provider SDK client."""

    @abc.abstractmethod
    async def _run_session(self, messages: List[Dict[str, Any]], config: Dict[str, Any],
                           tenant_id: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Run one managed session to completion; return a canonical result."""

    async def _dispatch_upstream_call(self, messages, model_id, temperature, max_tokens,
                                      tools, stop_sequences, cache_handle, config, **kwargs):
        return await self._run_session(messages, config, kwargs["tenant_id"], kwargs)

    async def _dispatch_upstream_stream(self, messages, model_id, temperature, max_tokens, config, **kwargs):
        out = await self._run_session(messages, config, kwargs["tenant_id"], kwargs)
        yield out.get("text", "")


class DelegatedAgent(BaseAgent):
    """An agent whose model step is a managed runtime. Everything else is BaseAgent's.
    The runtime is the agent's own (bound with the agent's read tools), so the rest of
    the workflow keeps using config['llm_service'].

    Caveat: this overrides `_resolve_llm_service`, a protected E2A seam that is not a
    declared extension hook and may change. Once E2A resolves configuration per agent
    (proposal P3: `scopes.agent.<Agent>.llm_service`), bind the runtime there instead and
    drop this class."""

    def __init__(self, runtime: DelegatedRuntime) -> None:
        self.runtime = runtime

    def _resolve_llm_service(self, config=None, **kwargs):
        return self.runtime
