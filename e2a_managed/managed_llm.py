"""ManagedLLMService: the shared shape of every frontier-provider model adapter.

E2A's BaseLLMAgentService already owns everything that is not wire transport: prompt
sanitising, context budget, KV-cache handle, tool-schema compilation, tenant and pod
permits, RPM/TPM quota, the circuit breaker, the upstream deadline, DLP and usage
accounting. A provider adapter implements only _dispatch_upstream_call/_stream.

This class narrows that further to four provider hooks, and settles three questions
once for all providers:

1. Who retries. Until E2A retries model calls itself (E2A v1.5.1, proposal P7), the
   provider SDKs do: `llm_sdk_max_retries` defaults to 2, and the SDKs honour Retry-After.
   E2A v1.5.0 calls the model with max_retries=0, so setting this to 0 today means NO layer
   retries a 429 or a 503. Once E2A v1.5.1 is pinned, set it to 0: one retry authority,
   which also keeps throttling from being hidden from E2A's classification.
2. Per-call vendor parameters. Every vendor key is resolved with
   self._resolve_param(key, config, kwargs): a keyword passed to agent.run(...) or
   process_inference(...) wins over the profile and the environment for that call.
3. Refusals. A provider decline is a typed ModelRefusal, retried once on
   `fallback_model_id` when one is configured; never treated as an answer.

Subclasses implement:
    _build_client(config, kwargs)                     -> SDK client (never at import time)
    _build_request(messages, model_id, ..., config, kwargs) -> provider request dict
    _send(client, request)                            -> provider response (awaitable)
    _parse(response)                                  -> canonical result (codec.*_to_result)
    _stream(client, request)                          -> async iterator of text chunks
"""
import abc
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional

from e2a import BaseLLMAgentService

from . import config_keys  # noqa: F401


class ModelRefusal(RuntimeError):
    """The managed model declined the request (provider safety policy)."""

    def __init__(self, provider: str, model: str, detail: str = "") -> None:
        super().__init__(f"{provider} model {model} declined the request{': ' + detail if detail else ''}")
        self.provider, self.model, self.detail = provider, model, detail


class ManagedLLMService(BaseLLMAgentService):
    provider: str = "abstract"
    platform: str = ""
    capabilities: FrozenSet[str] = frozenset()

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._client = client               # injected in tests / offline runners; else built lazily

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

    async def _dispatch_upstream_call(self, messages, model_id, temperature, max_tokens,
                                      tools, stop_sequences, cache_handle, config, **kwargs) -> Dict[str, Any]:
        client = self._sdk_client(config, kwargs)
        request = self._build_request(messages, model_id, temperature, max_tokens, tools,
                                      list(stop_sequences or []), cache_handle, config, kwargs)
        out = self._parse(await self._send(client, request))
        if (out.get("metadata") or {}).get("stop_reason") == "refusal":
            fallback = self._param("fallback_model_id", config, kwargs)
            if not fallback or fallback == model_id:
                raise ModelRefusal(self.provider, model_id, out.get("text", ""))
            retry = self._build_request(messages, fallback, temperature, max_tokens, tools,
                                        list(stop_sequences or []), cache_handle, config, kwargs)
            out = self._parse(await self._send(client, retry))
            if (out.get("metadata") or {}).get("stop_reason") == "refusal":
                raise ModelRefusal(self.provider, fallback, out.get("text", ""))
            out["metadata"]["fallback_from"] = model_id
        return out

    async def _dispatch_upstream_stream(self, messages, model_id, temperature, max_tokens, config, **kwargs):
        client = self._sdk_client(config, kwargs)
        request = self._build_request(messages, model_id, temperature, max_tokens, kwargs.get("tools"),
                                      list(kwargs.get("stop_sequences") or []), None, config, kwargs)
        async for chunk in self._stream(client, request):
            yield chunk
