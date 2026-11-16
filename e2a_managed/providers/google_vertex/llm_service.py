"""Gemini on Vertex AI, through the unified `google-genai` SDK (vertexai=True).

    VertexGeminiLLMService      generate_content / generate_content_stream
    VertexGeminiEmbeddingModel  gemini-embedding / text-embedding with RETRIEVAL_* task types

Auth: Application Default Credentials (Cloud Run service account, Workload Identity
Federation from GitHub in CI). Project and location from config (`gcp_project`,
`gcp_location`); `global` is a valid location for Gemini.

Mapping decisions
  * system messages -> system_instruction; tool results are answered by function NAME.
  * reasoning_effort -> thinking_level (Gemini 3+) or a thinking_budget (Gemini 2.5). Each model
    accepts only some levels (Google, "Thinking"): a level the model does not accept is lowered
    to the nearest one it does (E2A's "max" is the model's highest), never sent as is.
  * E2A cache handle -> `cached_content` only when a Vertex context cache name is given
    per call (kwargs['cached_content']); Gemini also caches implicitly.
  * response_schema -> response_mime_type=application/json + response_json_schema.
  * temperature IS sent (Gemini still accepts sampling parameters).
  * a SAFETY / PROHIBITED_CONTENT finish is a refusal (ModelRefusal; E2A routes it to fallback_model_id).
"""
from typing import Any, AsyncIterator, Dict, FrozenSet, List

from e2a import resolve_config

from ...capabilities import Capability as C
from ...codec import gemini_to_result, messages_to_gemini, tools_to_gemini
from ...managed_llm import ManagedLLMService
from ...managed_rag import ManagedEmbeddingModel

_BUDGET = {"minimal": 0, "low": 1024, "medium": 8192, "high": 24576, "xhigh": 32768, "max": -1}

# thinking levels each model accepts (Google, "Thinking", checked 16 Nov 2026); longest prefix first.
# A model not listed is assumed to accept low, medium and high, the set every current model shares.
_ORDER = ("minimal", "low", "medium", "high", "xhigh")
_LEVELS = {
    "gemini-4-hydrogen": ("low", "medium", "high", "xhigh"),
    "gemini-3.6-flash": ("minimal", "low", "medium", "high"),
    "gemini-3.5-flash-lite": ("minimal", "low", "medium", "high"),
    "gemini-3.1-flash-lite": ("minimal", "low", "medium", "high"),
    "gemini-3-flash-preview": ("minimal", "low", "medium", "high"),
    "gemini-3-pro-preview": ("low", "high"),
}
_DEFAULT_LEVELS = ("low", "medium", "high")


def thinking_level(model_id: str, effort: str) -> str:
    """E2A's effort as a thinking_level the model accepts: the requested level when it does,
    else the nearest lower one (cost never rises), else the model's lowest. "max" is the highest."""
    levels = next((v for k, v in sorted(_LEVELS.items(), key=lambda kv: -len(kv[0]))
                   if model_id.startswith(k)), _DEFAULT_LEVELS)
    if effort == "max":
        return levels[-1].upper()
    rank = _ORDER.index(effort) if effort in _ORDER else _ORDER.index("medium")
    lower = [lvl for lvl in levels if _ORDER.index(lvl) <= rank]
    return (lower[-1] if lower else levels[0]).upper()


def vertex_client(config: Dict[str, Any], kwargs: Dict[str, Any] = None, **http: Any):
    from google import genai
    from google.genai import types
    return genai.Client(vertexai=True, project=resolve_config("gcp_project", config, kwargs or {}),
                        location=resolve_config("gcp_location", config, kwargs or {}),
                        http_options=types.HttpOptions(**http) if http else None)


class VertexGeminiLLMService(ManagedLLMService):
    provider = "google"
    platform = "vertex_ai"
    capabilities: FrozenSet[str] = frozenset({C.TOOL_USE, C.STRUCTURED_OUTPUT, C.STREAMING, C.PROMPT_CACHING,
                                              C.REASONING_EFFORT, C.REFUSAL_FALLBACK})
    features = {"structured_output": "native", "sampling": "supported", "reasoning": "thinking"}   # E2A v1.6.1

    def _build_client(self, config, kwargs):
        from google.genai import types
        retries = int(self._param("llm_sdk_max_retries", config, kwargs))
        http = {"timeout": int(float(self._param("llm_sdk_timeout", config, kwargs)) * 1000)}
        if retries:
            http["retry_options"] = types.HttpRetryOptions(attempts=retries + 1)
        return vertex_client(config, kwargs, **http)

    def _build_request(self, messages, model_id, temperature, max_tokens, tools, stop_sequences,
                       cache_handle, config, kwargs) -> Dict[str, Any]:
        system, contents = messages_to_gemini(messages)
        cfg: Dict[str, Any] = {"max_output_tokens": int(max_tokens),
                               "labels": {"e2a-tenant": str(kwargs.get("tenant_id", "unknown")).lower()[:63]}}
        if temperature is not None:
            cfg["temperature"] = float(temperature)
        if system:
            cfg["system_instruction"] = system
        if tools:
            cfg["tools"] = tools_to_gemini(tools)
        if stop_sequences:
            cfg["stop_sequences"] = stop_sequences
        effort = self._effort(self._param("reasoning_effort", config, kwargs))
        if effort:
            cfg["thinking_config"] = ({"thinking_budget": _BUDGET.get(effort, 8192)} if model_id.startswith("gemini-2")
                                      else {"thinking_level": thinking_level(model_id, effort)})
        if kwargs.get("response_schema"):
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = kwargs["response_schema"]
        if kwargs.get("cached_content"):
            cfg["cached_content"] = kwargs["cached_content"]
        return {"model": model_id, "contents": contents, "config": cfg}

    async def _send(self, client, request):
        return await client.aio.models.generate_content(**request)

    def _parse(self, response) -> Dict[str, Any]:
        return gemini_to_result(response)

    async def _stream(self, client, request) -> AsyncIterator[str]:
        async for chunk in await client.aio.models.generate_content_stream(**request):
            if chunk.text:
                yield chunk.text


class VertexGeminiEmbeddingModel(ManagedEmbeddingModel):
    default_model_id = "gemini-embedding-001"

    def _build_client(self, config):
        return vertex_client(config)

    def _embed(self, texts: List[str], task: str) -> List[List[float]]:
        cfg: Dict[str, Any] = {"task_type": task}
        dims = resolve_config("embedding_dimensions", self.config)
        if dims:
            cfg["output_dimensionality"] = int(dims)
        resp = self.client.models.embed_content(model=self.model_id, contents=texts, config=cfg)
        return [list(e.values) for e in resp.embeddings]

    def embed_documents(self, texts):
        return [v for t in texts for v in self._embed([t], "RETRIEVAL_DOCUMENT")]

    def embed_query(self, text):
        return self._embed([text], "RETRIEVAL_QUERY")[0]
