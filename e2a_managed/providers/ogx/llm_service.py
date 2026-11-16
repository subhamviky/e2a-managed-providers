"""OGX (formerly Llama Stack): an open, customer-run agentic API server in front of any model.

OGX 1.x serves OpenAI-compatible endpoints, declared stable: /v1/chat/completions,
/v1/embeddings, /v1/files, /v1/vector_stores, /v1/responses, /v1/prompts. This profile
therefore talks to it with the standard OpenAI SDK (the same wire the repository already
tests for OpenAI), not a project-specific client. The inference provider behind it (Amazon
Bedrock here, or self-hosted Llama weights on vLLM) is the distribution's choice
(distribution/config.yaml); this code does not change with it.

    OGXLLMService          /v1/chat/completions             -> BaseLLMAgentService
    OGXEmbeddingModel      /v1/embeddings                   -> BaseEmbeddingModel
    OGXVectorIndex         /v1/files + /v1/vector_stores    -> BaseVectorIndex (one store per namespace)
    OGXPromptRegistry      /v1/prompts                      -> BasePromptRegistry
    OGXResponsesRuntime    /v1/responses (server-side loop) -> DelegatedRuntime

Tenancy. OGX partitions stored rows by tenant when `server.tenancy.mode` is `multi`; the
distribution here trusts an upstream header, because only the E2A services can reach the
server (private subnets, security group). Every call carries the tenant in `x-tenant-id`:
the request's tenant for model calls, the namespace for vector stores, and a fixed service
tenant (`ogx_service_tenant`) for calls that have none (embeddings, prompts).

Guardrails. OGX 1.x removed its Safety and Shields APIs; Responses guardrails call an
OpenAI-compatible moderation endpoint configured on the server, switched on per request
(`ogx_guardrails`). E2A's always-on stage on AWS is Bedrock Guardrails (the profile binds it).
"""
import re
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional

from e2a import BasePromptRegistry, resolve_config

from ...capabilities import Capability as C
from ...codec import as_dict, messages_to_openai_chat, openai_chat_to_result, tools_to_openai_chat
from ...managed_llm import ManagedLLMService
from ...managed_rag import ManagedEmbeddingModel, ManagedVectorIndex
from ..openai_azure.delegated_runtime import ResponsesAgentRuntime

TENANT_HEADER = "x-tenant-id"
_TENANT_OK = re.compile(r"[^a-z0-9_-]")


def ogx_tenant(value: Any) -> str:
    """A tenant id OGX accepts: lowercase, [a-z0-9][a-z0-9-_]{0,127}."""
    tenant = _TENANT_OK.sub("-", str(value).strip().lower())
    tenant = tenant if tenant[:1].isalnum() else f"t{tenant}"
    return tenant[:128]


def ogx_base_url(url: str) -> str:
    url = str(url).rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def tenant_headers(tenant: Any) -> Dict[str, str]:
    return {TENANT_HEADER: ogx_tenant(tenant)}


def ogx_client(config: Dict[str, Any], kwargs: Optional[Dict[str, Any]] = None, async_: bool = False, **opts):
    """The OpenAI SDK pointed at the OGX server. The key is OGX's own (unset when the server
    trusts the upstream header only); a placeholder satisfies the SDK, never a real secret."""
    from openai import AsyncOpenAI, OpenAI
    cls = AsyncOpenAI if async_ else OpenAI
    return cls(base_url=ogx_base_url(resolve_config("ogx_url", config, kwargs or {})),
               api_key=resolve_config("ogx_api_key", config, kwargs or {}) or "unused", **opts)


def _service_tenant(config: Dict[str, Any]) -> Dict[str, str]:
    return tenant_headers(resolve_config("ogx_service_tenant", config))


class OGXLLMService(ManagedLLMService):
    provider = "ogx"
    platform = "ogx"
    capabilities: FrozenSet[str] = frozenset({C.TOOL_USE, C.STRUCTURED_OUTPUT, C.STREAMING, C.REFUSAL_FALLBACK})
    # no reasoning control on the chat wire, so E2A drops reasoning_effort before the request
    features = {"structured_output": "native", "sampling": "supported", "reasoning": "none"}

    def _build_client(self, config, kwargs):
        return ogx_client(config, kwargs, async_=True, **self._sdk_options(config, kwargs))

    def _build_request(self, messages, model_id, temperature, max_tokens, tools, stop_sequences,
                       cache_handle, config, kwargs) -> Dict[str, Any]:
        req: Dict[str, Any] = {"model": model_id, "messages": messages_to_openai_chat(messages),
                               "max_tokens": int(max_tokens)}
        if temperature is not None:
            req["temperature"] = float(temperature)
        if tools:
            req["tools"] = tools_to_openai_chat(tools)
        if stop_sequences:
            req["stop"] = stop_sequences
        if kwargs.get("response_schema"):
            req["response_format"] = {"type": "json_schema", "json_schema": {"name": "e2a_output",
                                                                            "schema": kwargs["response_schema"]}}
        tenant = kwargs.get("tenant_id")
        req["extra_headers"] = tenant_headers(tenant) if tenant else _service_tenant(config)
        return req

    async def _send(self, client, request):
        return await client.chat.completions.create(**request)

    def _parse(self, response) -> Dict[str, Any]:
        return openai_chat_to_result(response, getattr(response, "_request_id", None))

    async def _stream(self, client, request) -> AsyncIterator[str]:
        stream = await client.chat.completions.create(**request, stream=True)
        async for chunk in stream:
            for choice in as_dict(chunk).get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


class OGXEmbeddingModel(ManagedEmbeddingModel):
    default_model_id = "amazon.titan-embed-text-v2:0"   # whatever embedding model the distribution registers

    def _build_client(self, config):
        return ogx_client(config, max_retries=int(resolve_config("llm_sdk_max_retries", config)))

    def _embed(self, texts: List[str]) -> List[List[float]]:
        args: Dict[str, Any] = {"model": self.model_id, "input": texts, "extra_headers": _service_tenant(self.config)}
        dims = resolve_config("embedding_dimensions", self.config)
        if dims:
            args["dimensions"] = int(dims)
        data = as_dict(self.client.embeddings.create(**args)).get("data", [])
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]

    def embed_documents(self, texts):
        return self._embed(list(texts)) if texts else []

    def embed_query(self, text):
        return self._embed([text])[0]


class OGXVectorIndex(ManagedVectorIndex):
    """One OGX vector store per namespace (`<index>-<namespace>`), on OGX's stable Files and
    Vector Stores APIs. Each chunk is one file, so it can be replaced and deleted on its own
    (the old Llama Stack vector_io had no delete). OGX embeds the text with the store's
    embedding model (set when the store is created), so the vectors E2A computed are not
    sent. The namespace is also the OGX tenant of every call, so stores of different
    namespaces sit in different tenant partitions."""
    capabilities: FrozenSet[str] = frozenset({C.VECTOR_SEARCH, C.HYBRID_SEARCH})

    def __init__(self, config=None, client=None):
        super().__init__(config, client)
        self._stores: Dict[str, str] = {}

    def _build_client(self, config):
        return ogx_client(config, max_retries=int(resolve_config("llm_sdk_max_retries", config)))

    def _store(self, namespace: str, create: bool) -> Optional[str]:
        name = f"{self.index_name}-{namespace}"
        if name in self._stores:
            return self._stores[name]
        headers = tenant_headers(namespace)
        for store in self.client.vector_stores.list(limit=100, extra_headers=headers):
            if store.name == name:
                self._stores[name] = store.id
                return store.id
        if not create:
            return None
        body = {"embedding_model": resolve_config("embedding_model_id", self.config)
                or OGXEmbeddingModel.default_model_id}
        dims = resolve_config("embedding_dimensions", self.config)
        if dims:
            body["embedding_dimension"] = int(dims)
        store = self.client.vector_stores.create(name=name, metadata={"namespace": namespace},
                                                 extra_body=body, extra_headers=headers)
        self._stores[name] = store.id
        return store.id

    def _files_by(self, store: str, namespace: str, key: str, values) -> List[str]:
        wanted = {str(v) for v in values}
        return [f.id for f in self.client.vector_stores.files.list(vector_store_id=store, limit=100,
                                                                    extra_headers=tenant_headers(namespace))
                if str((getattr(f, "attributes", None) or {}).get(key)) in wanted]

    def _remove(self, store: str, namespace: str, file_ids: List[str]) -> None:
        headers = tenant_headers(namespace)
        for file_id in file_ids:
            self.client.vector_stores.files.delete(file_id, vector_store_id=store, extra_headers=headers)
            self.client.files.delete(file_id, extra_headers=headers)

    def upsert(self, namespace, chunks, vectors):
        store = self._store(namespace, create=True)
        headers = tenant_headers(namespace)
        chunks = list(chunks)
        self._remove(store, namespace, self._files_by(store, namespace, "chunk_id",
                                                      [c["metadata"]["chunk_id"] for c in chunks]))
        for c in chunks:
            meta = {k: str(v) for k, v in c["metadata"].items()}
            uploaded = self.client.files.create(file=(f"{meta['chunk_id']}.txt", c["text"].encode("utf-8")),
                                                purpose="assistants", extra_headers=headers)
            self.client.vector_stores.files.create(vector_store_id=store, file_id=uploaded.id, attributes=meta,
                                                   extra_headers=headers)

    def delete(self, namespace, doc_id):
        store = self._store(namespace, create=False)
        if store:
            self._remove(store, namespace, self._files_by(store, namespace, "doc_id", [doc_id]))

    def search(self, namespace, query_text, query_vector, top_k):
        store = self._store(namespace, create=False)
        if not store:
            return []
        resp = as_dict(self.client.vector_stores.search(
            vector_store_id=store, query=query_text, max_num_results=int(top_k),
            extra_body={"search_mode": resolve_config("ogx_search_mode", self.config)},
            extra_headers=tenant_headers(namespace)))
        out = []
        for hit in resp.get("data", []):
            attrs = hit.get("attributes") or {}
            text = " ".join(part.get("text", "") for part in hit.get("content") or [] if part.get("type") == "text")
            out.append({**attrs, "chunk_id": attrs.get("chunk_id") or hit.get("file_id"), "text": text,
                        "score": float(hit.get("score") or 0.0)})
        return out


class _Template:
    def __init__(self, text: str, version: Any) -> None:
        self.text, self.version = text, version

    def render(self, **variables: Any) -> str:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), self.text)


class OGXPromptRegistry(BasePromptRegistry):
    """OGX's stable /v1/prompts (versioned templates). The OpenAI SDK has no prompts
    resource, so the call goes through the client's generic GET."""
    capabilities: FrozenSet[str] = frozenset({C.PROMPT_REGISTRY})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config, self._client = dict(config or {}), client

    @property
    def client(self):
        if self._client is None:
            self._client = ogx_client(self.config)
        return self._client

    def get_prompt(self, prompt_id, version="latest", tenant_id=None, config=None, **kwargs):
        query = {} if version in (None, "latest") else {"version": int(version)}
        p = as_dict(self.client.get(f"/prompts/{prompt_id}", cast_to=object,
                                    options={"params": query, "headers": tenant_headers(tenant_id) if tenant_id
                                             else _service_tenant(self.config)}))
        return _Template(p.get("prompt") or "", p.get("version"))


class OGXResponsesRuntime(ResponsesAgentRuntime):
    """OGX's server-side Responses loop as the delegated runtime: the same loop as the OpenAI
    one, another client, plus the tenant header and OGX's guardrails flag."""
    provider = "ogx_responses"

    def _build_client(self, config, kwargs):
        return ogx_client(config, kwargs, async_=True,
                          max_retries=int(self._resolve_param("llm_sdk_max_retries", config, kwargs)))

    def _request_extras(self, config, tenant_id) -> Dict[str, Any]:
        extras: Dict[str, Any] = {"extra_headers": tenant_headers(tenant_id) if tenant_id else _service_tenant(config)}
        if self._resolve_param("ogx_guardrails", config, {}):
            extras["extra_body"] = {"guardrails": True}
        return extras
