"""Gemini through the Gemini Developer API (AI Studio key), with the same `google-genai` SDK as
Vertex AI (hub design note GCP-01).

    GeminiAPILLMService      generate_content / generate_content_stream (vertexai=False)
    GeminiAPIEmbeddingModel  gemini-embedding through the Developer API

Why a profile of its own: the Developer API has a free tier (rate-limited; on it Google may use
prompts to improve its products, so synthetic data only), and the Google Cloud trial credit does
not pay for Gemini on Vertex AI. Everything else is the Vertex adapter's mapping (system
instruction, tools, thinking levels, structured output, refusals), with two differences:

  * auth is an API key (config `gemini_api_key`, env GEMINI_API_KEY; on Cloud Run a Secret
    Manager secret mounted as that variable), never Application Default Credentials;
  * request `labels` are a Vertex AI feature and are not sent.

Free-tier limits are enforced before a call leaves the process: E2A's `max_rpm` / `max_tpm`
(the profile sets them to the free tier's), and `gemini_max_requests_per_day`, a per-process
daily budget; a call over it fails as HTTP 429 (throttled), so E2A's retry policy and the
agent's fallback treat it like the service's own quota error.
"""
import datetime as dt
import threading
from typing import Any, Dict, FrozenSet, List

from e2a import resolve_config

from ...capabilities import Capability as C
from ..google_vertex.llm_service import VertexGeminiEmbeddingModel, VertexGeminiLLMService


class DailyQuotaExhausted(RuntimeError):
    """The process's daily request budget for the model is spent (free tier)."""
    status_code = 429


class _DailyBudget:
    def __init__(self) -> None:
        self._lock, self._day, self._used = threading.Lock(), None, 0

    def take(self, limit: int, today: dt.date = None) -> None:
        today = today or dt.datetime.now(dt.timezone.utc).date()
        with self._lock:
            if self._day != today:
                self._day, self._used = today, 0
            if limit and self._used >= limit:
                raise DailyQuotaExhausted(f"daily Gemini request budget of {limit} spent; resets at 00:00 UTC")
            self._used += 1


def gemini_api_client(config: Dict[str, Any], kwargs: Dict[str, Any] = None, **http: Any):
    from google import genai
    from google.genai import types
    key = resolve_config("gemini_api_key", config, kwargs or {})
    if not key:
        raise ValueError("gemini_api_key (GEMINI_API_KEY) is not set: the Gemini Developer API needs an API key")
    return genai.Client(api_key=key, http_options=types.HttpOptions(**http) if http else None)


class GeminiAPILLMService(VertexGeminiLLMService):
    provider = "google"
    platform = "gemini_api"
    capabilities: FrozenSet[str] = frozenset({C.TOOL_USE, C.STRUCTURED_OUTPUT, C.STREAMING,
                                              C.REASONING_EFFORT, C.REFUSAL_FALLBACK})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._budget = _DailyBudget()

    def _build_client(self, config, kwargs):
        from google.genai import types
        retries = int(self._param("llm_sdk_max_retries", config, kwargs))
        http = {"timeout": int(float(self._param("llm_sdk_timeout", config, kwargs)) * 1000)}
        if retries:
            http["retry_options"] = types.HttpRetryOptions(attempts=retries + 1)
        return gemini_api_client(config, kwargs, **http)

    def _build_request(self, messages, model_id, temperature, max_tokens, tools, stop_sequences,
                       cache_handle, config, kwargs) -> Dict[str, Any]:
        request = super()._build_request(messages, model_id, temperature, max_tokens, tools, stop_sequences,
                                         cache_handle, config, kwargs)
        request["config"].pop("labels", None)             # Vertex AI only
        request["config"].pop("cached_content", None)     # a Vertex context-cache name; implicit caching still applies
        return request

    async def _send(self, client, request):
        self._budget.take(int(resolve_config("gemini_max_requests_per_day", self._config, {}) or 0))
        return await super()._send(client, request)


class GeminiAPIEmbeddingModel(VertexGeminiEmbeddingModel):
    def _build_client(self, config):
        return gemini_api_client(config)

    def _embed(self, texts: List[str], task: str) -> List[List[float]]:
        return super()._embed(texts, task)
