"""AWS platform services, validated against AWS's own service models (botocore Stubber
checks every request against the real API definition) and moto for DynamoDB."""
import io
import json

import boto3
import pytest
from botocore.stub import ANY, Stubber
from e2a import FirewallRefusal

from e2a_managed.clouds.aws.bedrock import (AgentCoreRuntime, BedrockGuardrailStage, BedrockKnowledgeBaseRAGPipeline,
                                            BedrockPromptRegistry, BedrockReranker, S3VectorsIndex,
                                            TitanEmbeddingModel)
from e2a_managed.managed_rag import ManagedRAGPipeline
from e2a import MessageLog, RequestContext


def client(service):
    return boto3.client(service, region_name="us-east-1")


def ctx_and_log():
    ctx = RequestContext.begin(tenant_id="acme")
    return ctx, MessageLog([], ctx.correlation_id, "test")


def test_titan_embeddings_request_shape():
    c = client("bedrock-runtime")
    model = TitanEmbeddingModel({"embedding_dimensions": 256}, client=c)
    with Stubber(c) as stub:
        stub.add_response("invoke_model", {"body": io.BytesIO(json.dumps({"embedding": [0.1, 0.2]}).encode()),
                                           "contentType": "application/json"},
                          {"modelId": "amazon.titan-embed-text-v2:0", "contentType": "application/json",
                           "accept": "application/json",
                           "body": json.dumps({"inputText": "refunds", "normalize": True, "dimensions": 256})})
        assert model.embed_query("refunds") == [0.1, 0.2]
    assert model.model_id == "amazon.titan-embed-text-v2:0"


def test_s3_vectors_upsert_search_and_tenant_filter():
    c = client("s3vectors")
    index = S3VectorsIndex({"s3_vector_bucket": "kb-e2a", "vector_index_name": "e2a"}, client=c)
    chunk = {"text": "Refunds are issued within 14 days.", "metadata": {"chunk_id": "pol.md#0", "doc_id": "pol.md"}}
    with Stubber(c) as stub:
        stub.add_response("put_vectors", {}, {"vectorBucketName": "kb-e2a", "indexName": "e2a", "vectors": [
            {"key": "pol.md#0", "data": {"float32": [1.0, 0.0]},
             "metadata": {"chunk_id": "pol.md#0", "doc_id": "pol.md", "namespace": "rag_acme", "text": chunk["text"]}}]})
        stub.add_response("query_vectors", {"vectors": [
            {"key": "pol.md#0", "distance": 0.1, "metadata": {"text": chunk["text"], "doc_id": "pol.md"}}],
            "distanceMetric": "cosine"},
            {"vectorBucketName": "kb-e2a", "indexName": "e2a", "topK": 3, "queryVector": {"float32": [1.0, 0.0]},
             "filter": {"namespace": "rag_acme"}, "returnMetadata": True, "returnDistance": True})
        index.upsert("rag_acme", [chunk], [[1.0, 0.0]])
        hits = index.search("rag_acme", "when are refunds issued", [1.0, 0.0], 3)
    # vector-only (E2A 1.6.1 plan): no client-side keyword fusion; the reranker adds keyword relevance
    assert hits == [{"chunk_id": "pol.md#0", "text": chunk["text"], "doc_id": "pol.md", "score": 0.9}]


def test_bedrock_rerank_orders_by_relevance():
    c = client("bedrock-agent-runtime")
    reranker = BedrockReranker({}, client=c)
    hits = [{"chunk_id": "a", "text": "credit limits"}, {"chunk_id": "b", "text": "refund window"}]
    with Stubber(c) as stub:
        stub.add_response("rerank", {"results": [{"index": 1, "relevanceScore": 0.9}, {"index": 0, "relevanceScore": 0.2}]},
                          {"queries": ANY, "sources": ANY, "rerankingConfiguration": {
                              "type": "BEDROCK_RERANKING_MODEL", "bedrockRerankingConfiguration": {
                                  "numberOfResults": 2, "modelConfiguration": {
                                      "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"}}}})
        ranked = reranker.rerank("refund", hits, 5)
    assert [h["chunk_id"] for h in ranked] == ["b", "a"]


def test_rag_pipeline_passes_the_query_to_the_managed_reranker():
    class Index:
        def search(self, ns, q, v, k):
            return [{"chunk_id": "x", "text": "Refunds within 14 days.", "score": 0.5}]

    class Model:
        model_id = "m"

        def embed_query(self, t):
            return [1.0]

    class Reranker:
        def rerank(self, query, hits, top_n, config=None):      # E2A's BaseReranker
            self.query = query
            return hits[:top_n]
    rr = Reranker()
    rag = ManagedRAGPipeline({}, Model(), Index(), rr)
    out = rag.retrieve("refund window?", {}, tenant_id="acme")
    assert rr.query == "refund window?" and "_query" not in out[0]
    assert rag.evaluate_answer("Refunds within 14 days.", out) == 1.0


def test_knowledge_base_retrieval_filters_by_tenant():
    c = client("bedrock-agent-runtime")
    rag = BedrockKnowledgeBaseRAGPipeline({"bedrock_knowledge_base_id": "KB12345678"}, client=c)
    with Stubber(c) as stub:
        stub.add_response("retrieve", {"retrievalResults": [{"content": {"text": "Refunds within 14 days."},
                                                             "score": 0.8, "metadata": {"policy_id": "POL-REF-1"}}]},
                          {"knowledgeBaseId": "KB12345678", "retrievalQuery": {"text": "refunds"},
                           "retrievalConfiguration": {"vectorSearchConfiguration": {
                               "numberOfResults": ANY, "overrideSearchType": "HYBRID",
                               "filter": {"equals": {"key": "tenant_id", "value": "acme"}}}}})
        out = rag.retrieve("refunds", {}, tenant_id="acme")
    assert out[0]["policy_id"] == "POL-REF-1"


@pytest.mark.parametrize("assessment,blocked", [
    ([{"contentPolicy": {"filters": [{"type": "PROMPT_ATTACK", "confidence": "HIGH", "action": "BLOCKED"}]}}], True),
    ([{"sensitiveInformationPolicy": {"piiEntities": [{"type": "EMAIL", "match": "a@b.c", "action": "ANONYMIZED"}], "regexes": []}}], False),
])
def test_bedrock_guardrail_blocks_or_masks(assessment, blocked):
    c = client("bedrock-runtime")
    stage = BedrockGuardrailStage({"bedrock_guardrail_id": "gr-1"}, client=c)
    ctx, log = ctx_and_log()
    with Stubber(c) as stub:
        stub.add_response("apply_guardrail", {"usage": {"topicPolicyUnits": 1, "contentPolicyUnits": 1,
                                                        "wordPolicyUnits": 0, "sensitiveInformationPolicyUnits": 1,
                                                        "sensitiveInformationPolicyFreeUnits": 0,
                                                        "contextualGroundingPolicyUnits": 0},
                                              "action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "mail {EMAIL}"}],
                                              "assessments": assessment},
                          {"guardrailIdentifier": "gr-1", "guardrailVersion": "DRAFT", "source": "INPUT",
                           "content": [{"text": {"text": "mail a@b.c"}}]})
        state = {"query": "mail a@b.c"}
        if blocked:
            with pytest.raises(FirewallRefusal, match="contentPolicy"):
                stage.check(state, ctx, log)
        else:
            assert stage.check(state, ctx, log)["query"] == "mail {EMAIL}"


def test_guardrail_outage_fails_closed():
    class Down:
        def apply_guardrail(self, **kw):
            raise ConnectionError("throttled")
    ctx, log = ctx_and_log()
    with pytest.raises(FirewallRefusal, match="unavailable"):
        BedrockGuardrailStage({"bedrock_guardrail_id": "g"}, client=Down()).check({"query": "hi"}, ctx, log)


def test_prompt_management_renders_the_default_variant():
    c = client("bedrock-agent")
    reg = BedrockPromptRegistry({}, client=c)
    with Stubber(c) as stub:
        stub.add_response("get_prompt", {
            "name": "router", "id": "PROMPT1234", "arn": "arn:aws:bedrock:us-east-1:123456789012:prompt/PROMPT1234",
            "version": "3", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
            "defaultVariant": "v", "variants": [{"name": "v", "templateType": "TEXT",
                                                 "templateConfiguration": {"text": {"text": "Classify: {{query}}"}}}]},
            {"promptIdentifier": "PROMPT1234", "promptVersion": "3"})
        tpl = reg.get_prompt("PROMPT1234", version="3")
    assert tpl.render(query="refund?") == "Classify: refund?" and tpl.version == "3"


async def test_agentcore_runtime_payload_contract():
    c = client("bedrock-agentcore")
    rt = AgentCoreRuntime({"delegated_runtime_id": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/o2c-abc"},
                          client=c)
    with Stubber(c) as stub:
        stub.add_response("invoke_agent_runtime", {
            "response": io.BytesIO(json.dumps({"text": "Refunds take 14 days [POL-REF-1].",
                                               "usage": {"input_tokens": 10, "output_tokens": 5}}).encode()),
            "contentType": "application/json", "statusCode": 200, "runtimeSessionId": "x" * 33},
            {"agentRuntimeArn": ANY, "runtimeSessionId": ANY, "payload": ANY})
        out = await rt.process_inference([{"role": "user", "content": "refunds?"}], tenant_id="acme")
    assert out["text"].startswith("Refunds") and out["tokens_used"] == 15
