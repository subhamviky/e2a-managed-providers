"""Meta Llama through a Llama Stack distribution, hosted on AWS.

Meta's managed surface for enterprises is Llama Stack: one OpenAI-compatible API server
(inference, embeddings, vector stores, safety shields, prompts, Responses API, MCP tool
runtime) whose *providers* are configured per distribution. This profile runs the
distribution as an ECS service on AWS with Amazon Bedrock as the inference provider for
the Llama models, Llama Guard as the safety shield, and pgvector/OpenSearch behind
vector_io (see distribution/run.yaml and the terraform). Swapping the inference provider
(Bedrock, Vertex Model Garden, Together, self-hosted vLLM) is a run.yaml change; this code
does not change.

    LlamaStackLLMService        chat completions (OpenAI wire)  -> BaseLLMAgentService
    LlamaStackEmbeddingModel    embeddings                      -> BaseEmbeddingModel
    LlamaStackVectorIndex       vector_io (one store per tenant namespace) -> BaseVectorIndex
    LlamaGuardStage             safety.run_shield (Llama Guard) -> IngressStage
    LlamaStackPromptRegistry    prompts API (versioned)         -> BasePromptRegistry
    LlamaStackResponsesRuntime  Responses API agent loop        -> DelegatedRuntime
"""
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional

from e2a import BasePromptRegistry, resolve_config

from ...capabilities import Capability as C
from ...codec import as_dict, messages_to_openai_chat, openai_chat_to_result, tools_to_openai_chat
from ...guardrails import ManagedGuardrailStage, Verdict
from ...managed_llm import ManagedLLMService
from ...managed_rag import ManagedEmbeddingModel, ManagedVectorIndex
from ..openai_azure.delegated_runtime import ResponsesAgentRuntime


def llama_client(config: Dict[str, Any], kwargs: Optional[Dict[str, Any]] = None, async_: bool = False, **opts):
    from llama_stack_client import AsyncLlamaStackClient, LlamaStackClient
    cls = AsyncLlamaStackClient if async_ else LlamaStackClient
    return cls(base_url=resolve_config("llama_stack_url", config, kwargs or {}),
               api_key=resolve_config("llama_stack_api_key", config, kwargs or {}), **opts)


class LlamaStackLLMService(ManagedLLMService):
    provider = "meta"
    platform = "llama_stack"
    capabilities: FrozenSet[str] = frozenset({C.TOOL_USE, C.STRUCTURED_OUTPUT, C.STREAMING, C.REFUSAL_FALLBACK})
    # E2A v1.6.1: no reasoning control, so E2A drops reasoning_effort before the request
    features = {"structured_output": "native", "sampling": "supported", "reasoning": "none"}

    def _build_client(self, config, kwargs):
        return llama_client(config, kwargs, async_=True, **self._sdk_options(config, kwargs))

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
        if kwargs.get("tenant_id"):
            req["user"] = str(kwargs["tenant_id"])
        return req

    async def _send(self, client, request):
        return await client.chat.completions.create(**request)

    def _parse(self, response) -> Dict[str, Any]:
        return openai_chat_to_result(response)

    async def _stream(self, client, request) -> AsyncIterator[str]:
        stream = await client.chat.completions.create(**request, stream=True)
        async for chunk in stream:
            for choice in as_dict(chunk).get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


class LlamaStackEmbeddingModel(ManagedEmbeddingModel):
    default_model_id = "amazon.titan-embed-text-v2:0"   # whatever embedding model run.yaml registers

    def _build_client(self, config):
        return llama_client(config)

    def _embed(self, texts: List[str]) -> List[List[float]]:
        args: Dict[str, Any] = {"model": self.model_id, "input": texts}
        dims = resolve_config("embedding_dimensions", self.config)
        if dims:
            args["dimensions"] = int(dims)
        data = as_dict(self.client.embeddings.create(**args)).get("data", [])
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]

    def embed_documents(self, texts):
        return self._embed(list(texts)) if texts else []

    def embed_query(self, text):
        return self._embed([text])[0]


class LlamaStackVectorIndex(ManagedVectorIndex):
    """One Llama Stack vector store per tenant namespace (`<index>-<namespace>`), created on
    first write. Search runs in the store's hybrid mode when its provider supports it.
    Llama Stack's vector_io has no chunk delete: a document is removed by re-indexing into
    a fresh store (blue/green) -- delete() says so instead of silently keeping stale chunks."""
    capabilities: FrozenSet[str] = frozenset({C.VECTOR_SEARCH, C.HYBRID_SEARCH})

    def __init__(self, config=None, client=None):
        super().__init__(config, client)
        self._stores: Dict[str, str] = {}

    def _build_client(self, config):
        return llama_client(config)

    def _store(self, namespace: str, create: bool) -> Optional[str]:
        name = f"{self.index_name}-{namespace}"
        if name in self._stores:
            return self._stores[name]
        fixed = resolve_config("llama_vector_store_id", self.config)
        if fixed:
            self._stores[name] = f"{fixed}-{namespace}"
            return self._stores[name]
        for store in self.client.vector_stores.list(limit=100):
            if store.name == name:
                self._stores[name] = store.id
                return store.id
        if not create:
            return None
        self._stores[name] = self.client.vector_stores.create(name=name, metadata={"namespace": namespace}).id
        return self._stores[name]

    def upsert(self, namespace, chunks, vectors):
        store = self._store(namespace, create=True)
        model = resolve_config("embedding_model_id", self.config) or LlamaStackEmbeddingModel.default_model_id
        self.client.vector_io.insert(vector_store_id=store, chunks=[
            {"chunk_id": c["metadata"]["chunk_id"], "content": c["text"], "embedding": list(v),
             "embedding_dimension": len(v), "embedding_model": model,
             "chunk_metadata": {"chunk_id": c["metadata"]["chunk_id"], "document_id": str(c["metadata"].get("doc_id"))},
             "metadata": {k: str(val) for k, val in c["metadata"].items()}} for c, v in zip(chunks, vectors)])

    def delete(self, namespace, doc_id):
        raise NotImplementedError("Llama Stack vector_io has no chunk delete: re-index into a fresh vector store")

    def search(self, namespace, query_text, query_vector, top_k):
        store = self._store(namespace, create=False)
        if not store:
            return []
        resp = as_dict(self.client.vector_io.query(vector_store_id=store, query=query_text,
                                                   params={"max_chunks": int(top_k), "mode": "hybrid"}))
        out = []
        for chunk, score in zip(resp.get("chunks", []), resp.get("scores", [])):
            meta = chunk.get("metadata") or {}
            content = chunk.get("content")
            out.append({**meta, "chunk_id": chunk.get("chunk_id") or meta.get("chunk_id"),
                        "text": content if isinstance(content, str) else str(content), "score": float(score)})
        return out


class LlamaGuardStage(ManagedGuardrailStage):
    """Llama Guard as a Llama Stack shield. A violation at level 'error' refuses."""
    name = "llama_guard"

    def _build_client(self, config):
        return llama_client(config)

    def _screen(self, text, ctx):
        resp = as_dict(self.client.safety.run_shield(shield_id=resolve_config("llama_shield_id", self.config),
                                                     messages=[{"role": "user", "content": text}]))
        violation = resp.get("violation")
        if violation and violation.get("violation_level") == "error":
            category = (violation.get("metadata") or {}).get("violation_type")
            return Verdict(False, [str(category)] if category else ["llama_guard"], detail=violation.get("user_message", ""))
        return Verdict(True)


class _Template:
    def __init__(self, text: str, version: Any) -> None:
        self.text, self.version = text, version

    def render(self, **variables: Any) -> str:
        import re
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), self.text)


class LlamaStackPromptRegistry(BasePromptRegistry):
    capabilities: FrozenSet[str] = frozenset({C.PROMPT_REGISTRY})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config, self._client = dict(config or {}), client

    @property
    def client(self):
        if self._client is None:
            self._client = llama_client(self.config)
        return self._client

    def get_prompt(self, prompt_id, version="latest", tenant_id=None, config=None, **kwargs):
        args = {} if version in (None, "latest") else {"version": int(version)}
        p = as_dict(self.client.prompts.retrieve(prompt_id, **args))
        return _Template(p.get("prompt") or "", p.get("version"))


class LlamaStackResponsesRuntime(ResponsesAgentRuntime):
    """Llama Stack's OpenAI-compatible Responses API (with its MCP tool runtime) as the
    delegated runtime: the same loop as the OpenAI one, another client."""
    provider = "llama_stack_responses"

    def _build_client(self, config, kwargs):
        return llama_client(config, kwargs, async_=True,
                            max_retries=int(self._resolve_param("llm_sdk_max_retries", config, kwargs)))
