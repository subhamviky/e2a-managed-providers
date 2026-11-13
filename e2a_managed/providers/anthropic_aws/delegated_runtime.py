"""Claude Managed Agents as the delegated model step of an E2A agent.

Available on Claude Platform on AWS (and Anthropic's own API); not on Amazon Bedrock,
where the AgentCore Runtime (clouds.aws.bedrock.AgentCoreRuntime) plays this role.

The agent and its environment are control-plane resources, version-controlled under
`managed_agents/` and synced by CI with `ant apply` (the IaC for this service). This
class is the data plane: one session per E2A inference, created from the stored agent
id (`delegated_runtime_id`) and environment (`delegated_runtime_environment`).

The agent's built-in toolset is switched off in its definition; it may only call the
custom tools declared there, which are the E2A agent's READ tools. Each
`agent.custom_tool_use` event is executed host-side through the tool's execute()
(tenant-scoped, typed outcome) and answered with `user.custom_tool_result`, so no
credential or system-of-record access ever reaches the Anthropic-hosted sandbox. Any
tool call that would need approval is denied: nobody is watching an E2A request.
"""
from typing import Any, Dict, List

from ...codec import result, split_system
from ...delegated import DelegatedRuntime


class ClaudeManagedAgentsRuntime(DelegatedRuntime):
    provider = "anthropic_managed_agents"

    def _build_client(self, config, kwargs):
        from anthropic import AsyncAnthropicAWS
        return AsyncAnthropicAWS(aws_region=self._resolve_param("aws_region", config, kwargs),
                                 workspace_id=self._resolve_param("anthropic_aws_workspace_id", config, kwargs),
                                 max_retries=int(self._resolve_param("llm_sdk_max_retries", config, kwargs)))

    async def _run_session(self, messages: List[Dict[str, Any]], config, tenant_id, kwargs) -> Dict[str, Any]:
        cfg = {**self._config, **(config or {})}
        client = self._client_for(cfg, kwargs)
        system, rest = split_system(messages)
        prompt = "\n\n".join(([f"Context from the operator:\n{system}"] if system else []) +
                             [str(m.get("content", "")) for m in rest if m.get("role") == "user"])
        session = await client.beta.sessions.create(
            agent=self._resolve_param("delegated_runtime_id", cfg, kwargs),
            environment_id=self._resolve_param("delegated_runtime_environment", cfg, kwargs),
            title=f"e2a {tenant_id} {kwargs.get('correlation_id', '')}".strip(),
            metadata={"tenant_id": str(tenant_id), "correlation_id": str(kwargs.get("correlation_id", ""))})
        max_turns = int(self._resolve_param("delegated_max_turns", cfg, kwargs))
        texts: List[str] = []
        turns, usage_in, usage_out, stop = 0, 0, 0, "end_turn"
        async with client.beta.sessions.events.stream(session_id=session.id) as stream:      # stream first
            await client.beta.sessions.events.send(session_id=session.id, events=[
                {"type": "user.message", "content": [{"type": "text", "text": prompt}]}])
            async for event in stream:
                kind = event.type
                if kind == "agent.message":
                    texts += [b.text for b in event.content if getattr(b, "type", "") == "text"]
                elif kind == "agent.custom_tool_use":
                    turns += 1
                    output = (await self._run_tool(event.name, dict(event.input or {}), tenant_id, cfg)
                              if turns <= max_turns else '{"error": "tool budget for this request exhausted"}')
                    await client.beta.sessions.events.send(session_id=session.id, events=[{
                        "type": "user.custom_tool_result", "custom_tool_use_id": event.id,
                        "content": [{"type": "text", "text": output}]}])
                elif kind in ("agent.tool_use", "agent.mcp_tool_use") and \
                        getattr(event, "evaluated_permission", None) == "ask":
                    await client.beta.sessions.events.send(session_id=session.id, events=[{
                        "type": "user.tool_confirmation", "tool_use_id": event.id, "result": "deny"}])
                elif kind == "span.model_request_end":
                    usage = getattr(event, "model_usage", None) or getattr(event, "usage", None)
                    usage_in += int(getattr(usage, "input_tokens", 0) or 0)
                    usage_out += int(getattr(usage, "output_tokens", 0) or 0)
                elif kind == "session.status_idle":
                    reason = getattr(getattr(event, "stop_reason", None), "type", "end_turn")
                    if reason != "requires_action":
                        stop = "max_tokens" if reason == "budget_reached" else "end_turn"
                        break
                elif kind == "session.status_terminated":
                    stop = "error"
                    break
        return result("\n".join(t for t in texts if t), input_tokens=usage_in, output_tokens=usage_out,
                      stop_reason=stop, request_id=session.id)
