"""Amazon Bedrock platform services, shared by every model provider on AWS.

Embeddings, vector storage, reranking, guardrails, prompt management and the AgentCore
gateway/runtime are AWS's managed services, not a model vendor's: the Anthropic profile
and the Meta (Llama) profile on AWS bind the same classes. All calls go through boto3
with the standard credential chain (task role, IRSA, GitHub OIDC in CI); no keys appear
in config. Clients are injectable, so tests use botocore's Stubber with the real
service models (request shapes are validated against AWS's own API definitions).

    TitanEmbeddingModel              BaseEmbeddingModel   Titan Text Embeddings V2
    S3VectorsIndex                   BaseVectorIndex      Amazon S3 Vectors (+ client-side keyword fusion)
    BedrockReranker                  ManagedReranker      Bedrock Rerank API (Cohere Rerank 3.5 / Amazon Rerank)
    BedrockKnowledgeBaseRAGPipeline  BaseRAGPipeline      Knowledge Bases: managed chunk/embed/hybrid search
    BedrockGuardrailStage            IngressStage         Bedrock Guardrails ApplyGuardrail
    BedrockPromptRegistry            BasePromptRegistry   Bedrock Prompt Management
    AgentCoreGatewayTool             MCPToolService       AgentCore Gateway (managed MCP endpoint, OAuth)
    AgentCoreRuntime                 DelegatedRuntime     AgentCore Runtime (hosted agent; tools via the Gateway)
"""
import json
import re
import uuid
from typing import Any, Dict, FrozenSet, List, Optional

from e2a import BasePromptRegistry, BaseRAGPipeline, resolve_config

from ...capabilities import Capability as C
from ...codec import result
from ...delegated import DelegatedRuntime
from ...guardrails import ManagedGuardrailStage, Verdict
from ...managed_rag import (ManagedEmbeddingModel, ManagedReranker, ManagedVectorIndex,
                            lexical_groundedness)
from ...mcp_gateway import BearerGatewayTool


def _boto(service: str, config: Dict[str, Any]):
    import boto3
    return boto3.client(service, region_name=resolve_config("aws_region", config))


# ------------------------------------------------------------------------ embeddings
class TitanEmbeddingModel(ManagedEmbeddingModel):
    default_model_id = "amazon.titan-embed-text-v2:0"

    def _build_client(self, config):
        return _boto("bedrock-runtime", config)

    def _embed(self, text: str) -> List[float]:
        body: Dict[str, Any] = {"inputText": text, "normalize": True}
        dims = resolve_config("embedding_dimensions", self.config)
        if dims:
            body["dimensions"] = int(dims)
        resp = self.client.invoke_model(modelId=self.model_id, body=json.dumps(body),
                                        contentType="application/json", accept="application/json")
        return json.loads(resp["body"].read())["embedding"]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]          # Titan V2 embeds one text per call

    def embed_query(self, text):
        return self._embed(text)


# ------------------------------------------------------------------------ vector index
class S3VectorsIndex(ManagedVectorIndex):
    """One S3 Vectors index; tenants are separated by a `namespace` metadata filter
    (POOL tenancy). SILO tenancy = one index per tenant, set via vector_index_name.
    S3 Vectors is vector-only, and so is this index: it declares vector_search, not
    hybrid_search. Keyword relevance is the reranker's job (BedrockReranker on E2A's
    BaseReranker port; the AWS profiles recommend `rerank`), not a client-side fusion here."""

    def _build_client(self, config):
        return _boto("s3vectors", config)

    @property
    def bucket(self) -> str:
        return resolve_config("s3_vector_bucket", self.config)

    def upsert(self, namespace, chunks, vectors):
        items = [{"key": c["metadata"]["chunk_id"], "data": {"float32": [float(x) for x in v]},
                  "metadata": {**{k: str(val) for k, val in c["metadata"].items()}, "namespace": namespace,
                               "text": c["text"]}} for c, v in zip(chunks, vectors)]
        for i in range(0, len(items), 500):                  # PutVectors batch limit
            self.client.put_vectors(vectorBucketName=self.bucket, indexName=self.index_name,
                                    vectors=items[i:i + 500])

    def delete(self, namespace, doc_id):
        keys, token = [], None
        while True:
            page = self.client.list_vectors(vectorBucketName=self.bucket, indexName=self.index_name,
                                            returnMetadata=True, **({"nextToken": token} if token else {}))
            keys += [v["key"] for v in page.get("vectors", [])
                     if (v.get("metadata") or {}).get("doc_id") == doc_id
                     and (v.get("metadata") or {}).get("namespace") == namespace]
            token = page.get("nextToken")
            if not token:
                break
        if keys:
            self.client.delete_vectors(vectorBucketName=self.bucket, indexName=self.index_name, keys=keys)

    def search(self, namespace, query_text, query_vector, top_k):
        resp = self.client.query_vectors(vectorBucketName=self.bucket, indexName=self.index_name,
                                         topK=int(top_k), queryVector={"float32": [float(x) for x in query_vector]},
                                         filter={"namespace": namespace}, returnMetadata=True, returnDistance=True)
        return [{"chunk_id": h["key"], "text": h["metadata"].get("text", ""),
                 **{k: v for k, v in h["metadata"].items() if k not in ("text", "namespace")},
                 "score": round(1 - float(h.get("distance", 0.0)), 4)}
                for h in resp.get("vectors", [])]


# ------------------------------------------------------------------------ rerank
class BedrockReranker(ManagedReranker):
    default_model = "cohere.rerank-v3-5:0"

    def _build_client(self, config):
        return _boto("bedrock-agent-runtime", config)

    def rerank(self, query, hits, top_n, config=None):
        if not hits:
            return []
        region = resolve_config("aws_region", self.config)
        model = self.model_id or self.default_model
        arn = model if model.startswith("arn:") else f"arn:aws:bedrock:{region}::foundation-model/{model}"
        resp = self.client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": h["text"]}}}
                     for h in hits],
            rerankingConfiguration={"type": "BEDROCK_RERANKING_MODEL", "bedrockRerankingConfiguration": {
                "numberOfResults": min(int(top_n), len(hits)), "modelConfiguration": {"modelArn": arn}}})
        return [dict(hits[r["index"]], rerank_score=float(r["relevanceScore"])) for r in resp["results"]]


# ------------------------------------------------------------------------ fully managed RAG
class BedrockKnowledgeBaseRAGPipeline(BaseRAGPipeline):
    """Knowledge Bases own chunking, embedding and hybrid search (data synced from S3), so
    _search_index calls Retrieve directly and no embedding port is used. Tenant isolation
    is a metadata filter on `tenant_id` (written by the ingestion job)."""
    namespace_prefix = "kb"
    capabilities: FrozenSet[str] = frozenset({C.MANAGED_RETRIEVAL, C.HYBRID_SEARCH})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config, self._client = dict(config or {}), client

    @property
    def client(self):
        if self._client is None:
            self._client = _boto("bedrock-agent-runtime", self.config)
        return self._client

    def _embed_query(self, query, config=None, **kwargs):
        return []                                            # embedded by the managed service

    def _search_index(self, query_text, query_vector, config=None, tenant_id=None, **kwargs):
        cfg = {**self.config, **(config or {})}
        resp = self.client.retrieve(
            knowledgeBaseId=resolve_config("bedrock_knowledge_base_id", cfg, kwargs),
            retrievalQuery={"text": query_text},
            retrievalConfiguration={"vectorSearchConfiguration": {
                "numberOfResults": int(kwargs.get("top_k", resolve_config("rag_top_k", cfg, kwargs))),
                "overrideSearchType": "HYBRID",
                "filter": {"equals": {"key": "tenant_id", "value": tenant_id}}}})
        return [{"chunk_id": (r.get("metadata") or {}).get("x-amz-bedrock-kb-chunk-id", str(i)),
                 "text": r["content"]["text"], "score": float(r.get("score", 0.0)),
                 **{k: v for k, v in (r.get("metadata") or {}).items() if not k.startswith("x-amz")}}
                for i, r in enumerate(resp.get("retrievalResults", []))]

    def _rerank(self, results, config=None, **kwargs):
        return sorted(results, key=lambda r: -r["score"])[:int(kwargs.get("rerank_top_n", 3))]

    def _evaluate_answer(self, answer, config=None, **kwargs):
        return lexical_groundedness(answer, kwargs.get("retrieved", []))


# ------------------------------------------------------------------------ guardrails
def _blocked(node: Any, policy: str = "guardrail") -> List[str]:
    """Policy names (contentPolicy, sensitiveInformationPolicy, ...) holding a BLOCKED item."""
    found: List[str] = []
    if isinstance(node, dict):
        if node.get("action") == "BLOCKED":
            found.append(policy)
        for key, value in node.items():
            found += _blocked(value, key if key.endswith("Policy") else policy)
    elif isinstance(node, list):
        for value in node:
            found += _blocked(value, policy)
    return found


class BedrockGuardrailStage(ManagedGuardrailStage):
    """ApplyGuardrail on the input: content filters (incl. prompt attack), denied topics,
    word filters, sensitive information (PII block or mask). A BLOCKED assessment refuses;
    an ANONYMIZED-only intervention continues with the masked text."""
    name = "bedrock_guardrail"

    def _build_client(self, config):
        return _boto("bedrock-runtime", config)

    def _screen(self, text, ctx):
        resp = self.client.apply_guardrail(
            guardrailIdentifier=resolve_config("bedrock_guardrail_id", self.config),
            guardrailVersion=str(resolve_config("bedrock_guardrail_version", self.config)),
            source="INPUT", content=[{"text": {"text": text}}])
        if resp.get("action") != "GUARDRAIL_INTERVENED":
            return Verdict(True)
        blocked = sorted(set(_blocked(resp.get("assessments") or [])))
        if blocked:
            return Verdict(False, blocked)
        outputs = resp.get("outputs") or []
        return Verdict(True, sanitized=outputs[0]["text"] if outputs else None)


# ------------------------------------------------------------------------ prompts
class _Template:
    def __init__(self, text: str, version: str) -> None:
        self.text, self.version = text, version

    def render(self, **variables: Any) -> str:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), self.text)


class BedrockPromptRegistry(BasePromptRegistry):
    """Prompts versioned in Bedrock Prompt Management, so a business owner can publish a
    new prompt version without a container rebuild (E2A's BasePromptRegistry intent)."""
    capabilities: FrozenSet[str] = frozenset({C.PROMPT_REGISTRY})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config, self._client = dict(config or {}), client

    @property
    def client(self):
        if self._client is None:
            self._client = _boto("bedrock-agent", self.config)
        return self._client

    def get_prompt(self, prompt_id, version="latest", tenant_id=None, config=None, **kwargs):
        prefix = resolve_config("bedrock_prompt_arn_prefix", {**self.config, **(config or {})}, kwargs)
        ident = f"{prefix.rstrip('/')}/{prompt_id}" if prefix and not prompt_id.startswith("arn:") else prompt_id
        args = {"promptIdentifier": ident}
        if version and version != "latest":
            args["promptVersion"] = str(version)
        resp = self.client.get_prompt(**args)
        variant = next((v for v in resp["variants"] if v["name"] == resp.get("defaultVariant")), resp["variants"][0])
        return _Template(variant["templateConfiguration"]["text"]["text"], str(resp.get("version", version)))


# ------------------------------------------------------------------------ AgentCore
class AgentCoreGatewayTool(BearerGatewayTool):
    """An MCP tool behind Bedrock AgentCore Gateway (inbound OAuth/JWT; the gateway's
    targets are the projects' own E2A MCP servers, declared in Terraform)."""
    gateway_url_key = "agentcore_gateway_url"


class AgentCoreRuntime(DelegatedRuntime):
    """An agent hosted on Bedrock AgentCore Runtime. Its tools are reached through the
    AgentCore Gateway (read-only targets), so no host-side tool loop is needed. Payload
    contract (implemented by the hosted agent's entrypoint):
        request  {"messages": [canonical], "tenant_id": str}
        response {"text": str, "usage": {"input_tokens", "output_tokens"}}"""
    provider = "aws_agentcore"

    def _build_client(self, config, kwargs):
        return _boto("bedrock-agentcore", config)

    async def _run_session(self, messages, config, tenant_id, session, kwargs):
        import asyncio
        cfg = {**self._config, **(config or {})}
        client = self._client_for(cfg, kwargs)
        args = {"agentRuntimeArn": self._resolve_param("delegated_runtime_id", cfg, kwargs),
                "runtimeSessionId": f"{tenant_id}-{kwargs.get('correlation_id') or uuid.uuid4()}".ljust(33, "0"),
                "payload": json.dumps({"messages": messages, "tenant_id": tenant_id}).encode()}
        qualifier = self._resolve_param("delegated_runtime_environment", cfg, kwargs)
        if qualifier:
            args["qualifier"] = qualifier
        resp = await asyncio.to_thread(client.invoke_agent_runtime, **args)
        body = resp["response"].read() if hasattr(resp.get("response"), "read") else resp.get("response", b"{}")
        data = json.loads(body or b"{}")
        usage = data.get("usage") or {}
        return result(data.get("text", ""), input_tokens=usage.get("input_tokens", 0),
                      output_tokens=usage.get("output_tokens", 0), stop_reason=data.get("stop_reason", "end_turn"),
                      request_id=(resp.get("ResponseMetadata") or {}).get("RequestId"))
