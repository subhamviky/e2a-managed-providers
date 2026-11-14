"""ManagedLLMService: the shared shape of every frontier-provider model adapter.

E2A's BaseLLMAgentService already owns everything that is not wire transport: prompt
sanitising, context budget, KV-cache handle, tool-schema compilation, tenant and pod
permits, RPM/TPM quota, the circuit breaker, the upstream deadline, DLP and usage
accounting. A provider adapter implements only _dispatch_upstream_call/_stream.

This class narrows that further to four provider hooks, and settles three questions
once for all providers:

1. Who retries. E2A (v1.5.1+) retries a model call by error class: throttled, overloaded and
   transient failures, honouring Retry-After within the deadline, and throttling never opens
   the circuit. So the SDKs' own retries are 0 (`llm_sdk_max_retries`): one retry authority,
   no multiplied load in an outage, and every failure visible to E2A's classification.
2. Per-call vendor parameters. Every vendor key is resolved with
   self._resolve_param(key, config, kwargs): a keyword passed to agent.run(...) or
   process_inference(...) wins over the profile and the environment for that call.
3. Refusals. A provider decline is E2A's typed ModelRefusal (status REFUSED, routed to the
   agent's _fallback). Answering it with another model is E2A's (v1.6.1 `model_routes`): a
   configured `fallback_model_id` becomes the route [{on: [refusal], to: <id>}] when the
   deployment sets no model_routes of its own. No adapter retries a refusal itself.
4. Features (E2A v1.6.1). Each adapter declares `features`: whether it has native structured
   output, accepts sampling parameters, and how it reasons. E2A drops or refuses what the
   model cannot take, before the request is built; the adapter maps the rest.

Subclasses implement:
    _build_client(config, kwargs)                     -> SDK client (never at import time)
    _build_request(messages, model_id, ..., config, kwargs) -> provider request dict
    _send(client, request)                            -> provider response (awaitable)
    _parse(response)                                  -> canonical result (codec.*_to_result)
    _stream(client, request)                          -> async iterator of text chunks
"""
import abc
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional

from e2a import BaseLLMAgentService, resolve_config
from e2a import ModelRefusal as _E2AModelRefusal

from . import config_keys  # noqa: F401


# E2A's own since v1.6.0: raised here, it becomes status REFUSED (never retried, not a failure),
# and BaseAgent routes it to the agent's _fallback.
ModelRefusal = _E2AModelRefusal


class ManagedLLMService(BaseLLMAgentService):
    provider: str = "abstract"
    platform: str = ""
    capabilities: FrozenSet[str] = frozenset()
    features: Dict[str, str] = {}

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client = client               # injected in tests / offline runners; else built lazily
        fallback = resolve_config("fallback_model_id", self._config)
        if fallback and not resolve_config("model_routes", self._config):
            self._config = {**self._config, "model_routes": [{"on": ["refusal"], "to": fallback}]}

    # ------------------------------------------------------------ provider hooks
    @abc.abstractmethod
    def _build_client(self, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Any:
        """Construct the provider SDK client from resolved config (credentials come from
        the platform's own chain: IAM role, managed identity, ADC)."""

    @abc.abstractmethod
    def _build_request(self, messages: List[Dict[str, Any]], model_id: str, temperature: Optional[float],
                       max_tokens: int, tools: Optional[List[Dict[str, Any]]], stop_sequences: List[str],
                       cache_handle: Optional[str], config: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Map the canonical request to the provider's request parameters."""

    @abc.abstractmethod
    async def _send(self, client: Any, request: Dict[str, Any]) -> Any:
        """One unary provider call."""

    @abc.abstractmethod
    def _parse(self, response: Any) -> Dict[str, Any]:
        """Provider response -> canonical result."""

    @abc.abstractmethod
    def _stream(self, client: Any, request: Dict[str, Any]) -> AsyncIterator[str]:
        """Async generator of text chunks."""

    # ------------------------------------------------------------ shared plumbing
    def capabilities_for(self, config: Dict[str, Any]) -> FrozenSet[str]:
        return self.capabilities

    def _sdk_client(self, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Any:
        if self._client is None:
            self._client = self._build_client({**self._config, **(config or {})}, kwargs)
        return self._client

    def _sdk_options(self, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {"max_retries": int(self._resolve_param("llm_sdk_max_retries", config, kwargs)),
                "timeout": float(self._resolve_param("llm_sdk_timeout", config, kwargs))}

    def _param(self, key: str, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Any:
        """A vendor parameter: call-time kwargs > profile config > env > default."""
        return self._resolve_param(key, config, kwargs)

    def _effort(self, effort: Optional[str]) -> Optional[str]:
        """Hook: E2A's reasoning_effort (minimal | low | medium | high | max) in the provider's
        vocabulary. Default: unchanged."""
        return effort

    async def _dispatch_upstream_call(self, messages, model_id, temperature, max_tokens,
                                      tools, stop_sequences, cache_handle, config, **kwargs) -> Dict[str, Any]:
        client = self._sdk_client(config, kwargs)
        request = self._build_request(messages, model_id, temperature, max_tokens, tools,
                                      list(stop_sequences or []), cache_handle, config, kwargs)
        out = self._parse(await self._send(client, request))
        if (out.get("metadata") or {}).get("stop_reason") == "refusal":
            raise ModelRefusal(self.provider, model_id, out.get("text", ""))
        return out

    async def _dispatch_upstream_stream(self, messages, model_id, temperature, max_tokens, config, **kwargs):
        client = self._sdk_client(config, kwargs)
        request = self._build_request(messages, model_id, temperature, max_tokens, kwargs.get("tools"),
                                      list(kwargs.get("stop_sequences") or []), None, config, kwargs)
        async for chunk in self._stream(client, request):
            yield chunk
