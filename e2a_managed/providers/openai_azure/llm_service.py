"""OpenAI models on Azure AI Foundry (Azure OpenAI), through the v1 API.

Azure's current guidance is the standard OpenAI client pointed at
`https://<resource>.openai.azure.com/openai/v1/` (no api-version), with the
*deployment name* as `model`. Auth is Entra ID (managed identity / workload identity,
`DefaultAzureCredential`) by default; an API key only when one is configured.

    AzureOpenAIResponsesLLMService   Responses API (GA on Azure): function tools, remote
                                     MCP tools, structured outputs, reasoning effort
    AzureOpenAIEmbeddingModel        text-embedding-3-large / -small deployments

Mapping decisions
  * E2A temperature is not sent: current OpenAI reasoning models do not accept sampling
    parameters; depth is `reasoning.effort` (E2A key `reasoning_effort`, per-call override).
  * E2A's cache handle -> `prompt_cache_key` (OpenAI caches prefixes automatically; the key
    routes requests with the same static prefix to the same cache).
  * `store` defaults to False (no server-side copy) unless a delegated runtime needs state.
  * remote MCP servers (`mcp_servers`) become `{"type": "mcp"}` tools: the service calls them
    itself. Only read tools may be exposed that way (E2A material actions stay in _act).
"""
import os
from typing import Any, AsyncIterator, Dict, FrozenSet, List

from ...capabilities import Capability as C
from ...codec import messages_to_openai_responses, openai_responses_to_result, tools_to_openai_responses
from ...managed_llm import ManagedLLMService
from ...managed_rag import ManagedEmbeddingModel
from e2a import ErrorClass, ModelRefusal, classify_error, resolve_config

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"

# OpenAI "Error codes": 429s that waiting will not clear (credit or spend or usage limit). Their
# error.type can be `insufficient_quota`. Retried as throttling they would only burn the deadline.
ACCOUNT_LIMIT_CODES = frozenset({"credit_balance_exhausted", "organization_spend_limit_exceeded",
                                 "project_spend_limit_exceeded", "organization_usage_limit_exceeded"})


def openai_error_code(error: BaseException) -> tuple:
    """(code, type) of an OpenAI SDK error: from the parsed attributes, else the raw body."""
    body = getattr(error, "body", None)
    inner = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else body
    inner = inner if isinstance(inner, dict) else {}
    return (getattr(error, "code", None) or inner.get("code"), getattr(error, "type", None) or inner.get("type"))


def classify_openai_error(error: BaseException) -> ErrorClass:
    """E2A's classification, except an account limit (spend, credit, usage) is not retried:
    it is `deterministic`, so it fails at once, is logged with its kind, and is not counted
    as an outage by the circuit."""
    status = getattr(error, "status_code", None)
    code, kind = openai_error_code(error)
    if status == 429 and (code in ACCOUNT_LIMIT_CODES or kind == "insufficient_quota"):
        return ErrorClass("deterministic")
    return classify_error(error)


def azure_v1_base_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    return endpoint if endpoint.endswith("/openai/v1") else f"{endpoint}/openai/v1"


def azure_openai_credential(config: Dict[str, Any], kwargs: Dict[str, Any], async_: bool = True):
    """API key when configured, else an Entra ID bearer-token provider."""
    key = resolve_config("azure_openai_api_key", config, kwargs)
    if key:
        return key
    if async_:
        from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    return get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SCOPE)


def mcp_tools(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tools = []
    for s in servers or []:
        tool = {"type": "mcp", "server_label": s["name"], "server_url": s["url"],
                "require_approval": s.get("require_approval", "never")}
        if s.get("token_env") and s["token_env"] in os.environ:
            tool["headers"] = {"Authorization": f"Bearer {os.environ[s['token_env']]}"}
        if s.get("allowed_tools"):
            tool["allowed_tools"] = s["allowed_tools"]
        tools.append(tool)
    return tools


class OpenAIResponsesLLMService(ManagedLLMService):
    """Responses API wire, independent of who hosts it (Azure, OpenAI, Llama Stack)."""
    provider = "openai"
    capabilities: FrozenSet[str] = frozenset({C.TOOL_USE, C.STRUCTURED_OUTPUT, C.STREAMING, C.PROMPT_CACHING,
                                              C.REASONING_EFFORT, C.REFUSAL_FALLBACK, C.MCP_CONNECTOR})
    # E2A v1.6.1 features. Sampling is not declared: the transport never sends temperature
    # (reasoning models reject it), so there is nothing for E2A to drop.
    features = {"structured_output": "native", "reasoning": "effort"}

    def _effort(self, effort):
        return "high" if effort == "max" else effort         # E2A's "max" is the top documented level

    def _classify_error(self, error):
        return classify_openai_error(error)

    def _build_request(self, messages, model_id, temperature, max_tokens, tools, stop_sequences,
                       cache_handle, config, kwargs) -> Dict[str, Any]:
        instructions, items = messages_to_openai_responses(messages)
        req: Dict[str, Any] = {"model": model_id, "input": items, "max_output_tokens": int(max_tokens),
                               "store": bool(self._param("openai_store_responses", config, kwargs))}
        if instructions:
            req["instructions"] = instructions
        wire_tools = tools_to_openai_responses(tools) + mcp_tools(self._param("mcp_servers", config, kwargs))
        if wire_tools:
            req["tools"] = wire_tools
        effort = self._effort(self._param("reasoning_effort", config, kwargs))
        if effort:
            req["reasoning"] = {"effort": effort}
        if kwargs.get("response_schema"):
            req["text"] = {"format": {"type": "json_schema", "name": "e2a_output",
                                      "schema": kwargs["response_schema"], "strict": False}}
        if cache_handle:
            req["prompt_cache_key"] = cache_handle
        return req

    async def _send(self, client, request):
        return await client.responses.create(**request)

    def _parse(self, response) -> Dict[str, Any]:
        return openai_responses_to_result(response, getattr(response, "_request_id", None))

    async def _stream(self, client, request) -> AsyncIterator[str]:
        stream = await client.responses.create(**request, stream=True)
        async for event in stream:
            if getattr(event, "type", "") == "response.output_text.delta":
                yield event.delta


class AzureOpenAIResponsesLLMService(OpenAIResponsesLLMService):
    """Microsoft Foundry's guardrails (DefaultV2 by default) filter every call. A blocked
    prompt is HTTP 400 with code `content_filter`; a blocked output is flagged in the
    response's top-level `content_filters` array (a Foundry extension the SDK does not type).
    Both are a policy outcome, so E2A reports them as REFUSED (ModelRefusal, routed by
    `model_routes`), not as a failure."""
    platform = "azure_ai_foundry"

    async def _send(self, client, request):
        try:
            return await client.responses.create(**request)
        except Exception as error:
            code, _ = openai_error_code(error)
            if getattr(error, "status_code", None) == 400 and code == "content_filter":
                raise ModelRefusal(self.provider, str(request.get("model", "")),
                                   "blocked by the platform's content filter", "content_filter") from error
            raise

    def _parse(self, response) -> Dict[str, Any]:
        result = super()._parse(response)
        filters = getattr(response, "content_filters", None)
        if filters is None:
            extra = getattr(response, "model_extra", None) or {}
            filters = extra.get("content_filters") if isinstance(extra, dict) else None
        if filters is None and isinstance(response, dict):
            filters = response.get("content_filters")
        if any((f.get("blocked") if isinstance(f, dict) else getattr(f, "blocked", False)) for f in filters or []):
            meta = result.setdefault("metadata", {})
            meta["stop_reason"], meta["refusal_category"] = "refusal", "content_filter"
            result["text"] = result.get("text") or "blocked by the platform's content filter"
        return result

    def _build_client(self, config, kwargs):
        from openai import AsyncOpenAI
        return AsyncOpenAI(base_url=azure_v1_base_url(self._param("azure_openai_endpoint", config, kwargs)),
                           api_key=azure_openai_credential(config, kwargs), **self._sdk_options(config, kwargs))


class AzureOpenAIEmbeddingModel(ManagedEmbeddingModel):
    default_model_id = "text-embedding-3-large"       # the Azure deployment name

    def _build_client(self, config):
        from openai import OpenAI
        return OpenAI(base_url=azure_v1_base_url(resolve_config("azure_openai_endpoint", config)),
                      api_key=azure_openai_credential(config, {}, async_=False),
                      max_retries=int(resolve_config("llm_sdk_max_retries", config)))

    def _embed(self, texts: List[str]) -> List[List[float]]:
        args: Dict[str, Any] = {"model": self.model_id, "input": texts}
        dims = resolve_config("embedding_dimensions", self.config)
        if dims:
            args["dimensions"] = int(dims)
        resp = self.client.embeddings.create(**args)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def embed_documents(self, texts):
        out: List[List[float]] = []
        for i in range(0, len(texts), 256):                     # batch inputs per request
            out += self._embed(texts[i:i + 256])
        return out

    def embed_query(self, text):
        return self._embed([text])[0]
