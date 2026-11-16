"""Claude on AWS, two ways — one adapter family, chosen by profile.

    BedrockClaudeLLMService        Amazon Bedrock (AWS-operated). The most common enterprise
                                   path: IAM, VPC endpoints, Bedrock Guardrails, CloudTrail.
                                   Model ids carry the `anthropic.` prefix.
    ClaudePlatformAWSLLMService    Claude Platform on AWS (Anthropic-operated, AWS IAM and
                                   billing, same-day parity with Anthropic's own API): adds
                                   the MCP connector, web search, batches and Managed
                                   Agents. Server-side refusal fallback is not offered on
                                   this platform (Anthropic documents it for the Claude API
                                   and Claude Models on Google Cloud, Anthropic managed):
                                   E2A's model_routes answer refusals, as on Bedrock.

Both send the Messages API through the official `anthropic` SDK. What E2A's base class
already does is not repeated here (quota, deadline, circuit breaker, DLP, permits).

Claude-specific mapping decisions
  * temperature is NOT sent: current Claude models reject sampling parameters; depth is
    controlled with `output_config.effort` (E2A key `reasoning_effort`, overridable per
    call: agent.run(..., reasoning_effort="high")).
  * thinking is left to the model default (adaptive on current models).
  * E2A's cache handle (a static prefix >= min_cache_threshold) becomes a cache_control
    breakpoint on the system prompt: E2A decides *what* is cacheable, Claude caches it.
  * response_schema -> output_config.format json_schema (structured outputs).
  * refusals: stop_reason "refusal" -> ModelRefusal; E2A routes it (fallback_model_id -> model_routes)
    on both platforms here. `server_side_fallbacks` (the `fallbacks` parameter, beta) is for a
    platform that offers it; a class that sets it declares refusal_fallback_server.
"""
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional

from ...capabilities import Capability as C
from ...codec import anthropic_to_result, messages_to_anthropic, tools_to_anthropic
from ...managed_llm import ManagedLLMService

MCP_BETA = "mcp-client-2025-11-20"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeMessagesLLMService(ManagedLLMService):
    provider = "anthropic"
    capabilities: FrozenSet[str] = frozenset({C.TOOL_USE, C.STRUCTURED_OUTPUT, C.STREAMING, C.PROMPT_CACHING,
                                              C.REASONING_EFFORT, C.REFUSAL_FALLBACK})
    server_side_fallbacks = False
    mcp_connector = False
    # E2A v1.6.1 features. Sampling is not declared: the transport never sends temperature
    # (current Claude models reject it), so there is nothing for E2A to drop.
    features = {"structured_output": "native", "reasoning": "effort"}

    def _betas(self, config: Dict[str, Any], kwargs: Dict[str, Any], mcp: bool) -> List[str]:
        betas = list(self._param("anthropic_betas", config, kwargs) or [])
        if mcp and MCP_BETA not in betas:
            betas.append(MCP_BETA)
        if self.server_side_fallbacks and FALLBACK_BETA not in betas:
            betas.append(FALLBACK_BETA)
        return betas

    def _effort(self, effort):
        return "low" if effort == "minimal" else effort      # Claude has no "minimal" level

    def _build_request(self, messages, model_id, temperature, max_tokens, tools, stop_sequences,
                       cache_handle, config, kwargs) -> Dict[str, Any]:
        system, wire = messages_to_anthropic(messages, cache_system=cache_handle is not None)
        req: Dict[str, Any] = {"model": model_id, "max_tokens": int(max_tokens), "messages": wire}
        if system:
            req["system"] = system
        if tools:
            req["tools"] = tools_to_anthropic(tools)
        if stop_sequences:
            req["stop_sequences"] = stop_sequences
        output_config: Dict[str, Any] = {}
        effort = self._effort(self._param("reasoning_effort", config, kwargs))
        if effort:
            output_config["effort"] = effort
        schema = kwargs.get("response_schema")
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            req["output_config"] = output_config
        servers = self._param("mcp_servers", config, kwargs) if self.mcp_connector else []
        if servers:
            import os
            req["mcp_servers"] = [{"type": "url", "url": s["url"], "name": s["name"],
                                   **({"authorization_token": os.environ[s["token_env"]]}
                                      if s.get("token_env") and s["token_env"] in os.environ else {})} for s in servers]
            req["tools"] = req.get("tools", []) + [{"type": "mcp_toolset", "mcp_server_name": s["name"]}
                                                   for s in servers]
        if self.server_side_fallbacks:
            req["fallbacks"] = "default"
        betas = self._betas(config, kwargs, bool(servers))
        if betas:
            req["betas"] = betas
        return req

    async def _send(self, client, request):
        if "betas" in request:
            return await client.beta.messages.create(**request)
        return await client.messages.create(**request)

    def _parse(self, response) -> Dict[str, Any]:
        return anthropic_to_result(response, getattr(response, "_request_id", None))

    async def _stream(self, client, request) -> AsyncIterator[str]:
        request = {k: v for k, v in request.items() if k != "fallbacks"}
        api = client.beta.messages if "betas" in request else client.messages
        async with api.stream(**request) as stream:
            async for text in stream.text_stream:
                yield text


class BedrockClaudeLLMService(ClaudeMessagesLLMService):
    """Claude on Amazon Bedrock through the Messages-API (Mantle) endpoint. Credentials:
    the standard AWS chain (task role, IRSA, OIDC in CI). No API key in config."""
    platform = "bedrock"

    def _build_client(self, config, kwargs):
        from anthropic import AsyncAnthropicBedrockMantle
        return AsyncAnthropicBedrockMantle(aws_region=self._param("aws_region", config, kwargs),
                                           **self._sdk_options(config, kwargs))


class ClaudePlatformAWSLLMService(ClaudeMessagesLLMService):
    """Claude Platform on AWS: SigV4 / IAM auth, AWS billing, Anthropic-operated API."""
    platform = "claude_platform_aws"
    mcp_connector = True
    capabilities = ClaudeMessagesLLMService.capabilities | {C.MCP_CONNECTOR, C.SERVER_WEB_SEARCH, C.BATCH}

    def _build_client(self, config, kwargs):
        from anthropic import AsyncAnthropicAWS
        return AsyncAnthropicAWS(aws_region=self._param("aws_region", config, kwargs),
                                 workspace_id=self._param("anthropic_aws_workspace_id", config, kwargs),
                                 **self._sdk_options(config, kwargs))
