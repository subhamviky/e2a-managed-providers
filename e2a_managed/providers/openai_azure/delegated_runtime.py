"""A Responses-API agent run as the delegated model step of an E2A agent.

The Responses API runs a hosted tool loop: remote MCP tools are called by the service
itself; function tools come back to the caller. This runtime:
  * offers the E2A agent's READ tools as function tools and answers each call host-side
    (tenant-scoped execute(), typed outcome) in the next request;
  * optionally adds remote MCP servers (`mcp_servers`, read-only, require_approval never);
  * stops at `delegated_max_turns` round trips; the E2A request deadline bounds the whole run.

The same class drives OpenAI on Azure and Llama Stack's OpenAI-compatible Responses API
(meta_llama binds a subclass that only changes the client).
"""
import json
from typing import Any, Dict, List

from ...codec import messages_to_openai_responses, openai_responses_to_result, tools_to_openai_responses
from ...delegated import DelegatedRuntime
from .llm_service import azure_openai_credential, azure_v1_base_url, mcp_tools


class ResponsesAgentRuntime(DelegatedRuntime):
    provider = "openai_responses_agent"

    def _build_client(self, config, kwargs):
        from openai import AsyncOpenAI
        return AsyncOpenAI(base_url=azure_v1_base_url(self._resolve_param("azure_openai_endpoint", config, kwargs)),
                           api_key=azure_openai_credential(config, kwargs),
                           max_retries=int(self._resolve_param("llm_sdk_max_retries", config, kwargs)))

    def _model(self, config, kwargs) -> str:
        return self._resolve_param("delegated_runtime_id", config, kwargs) or \
            self._resolve_param("model_id", config, kwargs)

    async def _run_session(self, messages: List[Dict[str, Any]], config, tenant_id, kwargs) -> Dict[str, Any]:
        cfg = {**self._config, **(config or {})}
        client = self._client_for(cfg, kwargs)
        instructions, items = messages_to_openai_responses(messages)
        tools = tools_to_openai_responses(self.tool_declarations()) + \
            mcp_tools(self._resolve_param("mcp_servers", cfg, kwargs))
        max_turns = int(self._resolve_param("delegated_max_turns", cfg, kwargs))
        effort = self._resolve_param("reasoning_effort", cfg, kwargs)
        used_in = used_out = 0
        for _turn in range(max_turns + 1):
            req: Dict[str, Any] = {"model": self._model(cfg, kwargs), "input": items, "store": False,
                                   "metadata": {"tenant_id": str(tenant_id)}}
            if instructions:
                req["instructions"] = instructions
            if tools:
                req["tools"] = tools
            if effort:
                req["reasoning"] = {"effort": effort}
            out = openai_responses_to_result(await client.responses.create(**req))
            used_in += out["metadata"]["input_tokens"]
            used_out += out["metadata"]["output_tokens"]
            calls = out.get("tool_calls") or []
            if not calls:
                out["metadata"].update(input_tokens=used_in, output_tokens=used_out, tokens_used=used_in + used_out)
                return out
            for call in calls:                          # replay the calls, then their outputs
                items.append({"type": "function_call", "call_id": call["id"], "name": call["name"],
                              "arguments": json.dumps(call["arguments"])})
                items.append({"type": "function_call_output", "call_id": call["id"],
                              "output": await self._run_tool(call["name"], call["arguments"], tenant_id, cfg)})
        raise RuntimeError(f"delegated run exceeded {max_turns} tool round trips")
