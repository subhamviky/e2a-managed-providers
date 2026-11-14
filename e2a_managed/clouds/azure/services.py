"""Azure platform services shared by every model provider on Azure.

    AzureAISearchIndex          BaseVectorIndex   Azure AI Search: native hybrid (BM25 + vector, RRF)
                                                  + semantic ranker, one index, tenant filter
    AzureSemanticRanker         ManagedReranker   reads the semantic ranker's score (no extra call)
    AzureContentSafetyStage     IngressStage      Prompt Shields (jailbreak / injection) + harm categories
    APIMGatewayTool             MCPToolService    Azure API Management in front of MCP servers
    AzureObservability, AzureInfraProvisioner, AzureReleasePipeline

The stores, broker and secret store come from e2a-adapters' "azure" profile, which openai_azure extends.

REST calls use httpx with Entra ID bearer tokens (or a key when configured). The
transport is injectable, so tests drive the exact request bodies the services document.
"""
import base64
import json
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

import httpx
from e2a import resolve_config

from ...capabilities import Capability as C
from ...guardrails import ManagedGuardrailStage, Verdict
from ...iac import TerraformInfraProvisioner
from ...managed_rag import ManagedReranker, ManagedVectorIndex
from ...mcp_gateway import ManagedMCPGatewayTool
from ...observability import ManagedObservability
from ...release import ManagedReleasePipeline

SEARCH_API = "2024-07-01"
CONTENT_SAFETY_API = "2024-09-01"
TERRAFORM = Path(__file__).parent / "terraform"


def _entra_token(scope: str) -> str:
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential().get_token(scope).token


class _AzureRest:
    def __init__(self, config: Optional[Dict[str, Any]] = None, transport: Any = None, **_: Any) -> None:
        self.config, self.transport = dict(config or {}), transport

    def _http(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=30.0)


# ------------------------------------------------------------------------ AI Search
def _key(chunk_id: str) -> str:
    """Document keys allow letters, digits, '_', '-', '='; chunk ids contain '#'."""
    return base64.urlsafe_b64encode(chunk_id.encode()).decode()


class AzureAISearchIndex(_AzureRest, ManagedVectorIndex):
    """Index fields: id (key), namespace (filterable), doc_id (filterable), chunk_id, text
    (searchable), metadata (string, JSON), embedding (vector). Created by Terraform/REST
    at provisioning time; this class only reads and writes documents."""
    capabilities: FrozenSet[str] = frozenset({C.VECTOR_SEARCH, C.HYBRID_SEARCH, C.RERANK})

    def __init__(self, config=None, transport=None, client=None):
        _AzureRest.__init__(self, config, transport)
        ManagedVectorIndex.__init__(self, config, client)

    def _build_client(self, config):
        return None

    def _url(self, op: str) -> str:
        base = resolve_config("azure_search_endpoint", self.config).rstrip("/")
        return f"{base}/indexes/{self.index_name}/docs/{op}?api-version={SEARCH_API}"

    def _headers(self) -> Dict[str, str]:
        key = resolve_config("azure_search_api_key", self.config)
        return {"api-key": key} if key else {"Authorization": f"Bearer {_entra_token('https://search.azure.com/.default')}"}

    def _post(self, op: str, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._http() as http:
            resp = http.post(self._url(op), json=body, headers=self._headers())
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    def upsert(self, namespace, chunks, vectors):
        docs = [{"@search.action": "mergeOrUpload", "id": _key(f"{namespace}/{c['metadata']['chunk_id']}"),
                 "namespace": namespace, "doc_id": c["metadata"].get("doc_id"), "chunk_id": c["metadata"]["chunk_id"],
                 "text": c["text"], "metadata": json.dumps(c["metadata"]), "embedding": list(v)}
                for c, v in zip(chunks, vectors)]
        for i in range(0, len(docs), 1000):
            self._post("index", {"value": docs[i:i + 1000]})

    def delete(self, namespace, doc_id):
        hits = self._post("search", {"search": "*", "filter": f"namespace eq '{namespace}' and doc_id eq '{doc_id}'",
                                     "select": "id", "top": 1000}).get("value", [])
        if hits:
            self._post("index", {"value": [{"@search.action": "delete", "id": h["id"]} for h in hits]})

    def search(self, namespace, query_text, query_vector, top_k):
        body = {"search": query_text, "filter": f"namespace eq '{namespace}'", "top": int(top_k),
                "select": "chunk_id,doc_id,text,metadata",
                "vectorQueries": [{"kind": "vector", "vector": list(query_vector), "fields": "embedding",
                                   "k": int(top_k) * 4}],
                "queryType": "semantic",
                "semanticConfiguration": resolve_config("azure_search_semantic_config", self.config)}
        out = []
        for h in self._post("search", body).get("value", []):
            meta = json.loads(h.get("metadata") or "{}")
            out.append({**meta, "chunk_id": h["chunk_id"], "text": h["text"], "score": float(h["@search.score"]),
                        "rerank_score": h.get("@search.rerankerScore")})
        return out


class AzureSemanticRanker(ManagedReranker):
    """The semantic ranker already scored the hits inside the hybrid query."""

    def _build_client(self, config):
        return None

    def rerank(self, query, hits, top_n, config=None):
        return sorted(hits, key=lambda h: -(h.get("rerank_score") or h.get("score") or 0.0))[:int(top_n)]


# ------------------------------------------------------------------------ Content Safety
class AzureContentSafetyStage(_AzureRest, ManagedGuardrailStage):
    """Prompt Shields refuses jailbreak / injection attacks; text analysis refuses harm
    categories at or above `severity_threshold` (0-7 scale, 4 = medium)."""
    name = "azure_content_safety"
    severity_threshold = 4

    def __init__(self, config=None, client=None, transport=None, fail_open=None):
        _AzureRest.__init__(self, config, transport)
        ManagedGuardrailStage.__init__(self, config, client, fail_open)

    def _build_client(self, config):
        return None

    def _call(self, op: str, body: Dict[str, Any]) -> Dict[str, Any]:
        base = resolve_config("azure_content_safety_endpoint", self.config).rstrip("/")
        key = resolve_config("azure_content_safety_key", self.config)
        headers = {"Ocp-Apim-Subscription-Key": key} if key else \
            {"Authorization": f"Bearer {_entra_token('https://cognitiveservices.azure.com/.default')}"}
        with self._http() as http:
            resp = http.post(f"{base}/contentsafety/text:{op}?api-version={CONTENT_SAFETY_API}", json=body,
                             headers=headers)
            resp.raise_for_status()
            return resp.json()

    def _screen(self, text, ctx):
        shield = self._call("shieldPrompt", {"userPrompt": text, "documents": []})
        if (shield.get("userPromptAnalysis") or {}).get("attackDetected"):
            return Verdict(False, ["prompt_attack"])
        analysis = self._call("analyze", {"text": text, "categories": ["Hate", "Sexual", "Violence", "SelfHarm"],
                                          "outputType": "EightSeverityLevels"})
        harmful = [c["category"] for c in analysis.get("categoriesAnalysis", [])
                   if int(c.get("severity") or 0) >= self.severity_threshold]
        return Verdict(not harmful, harmful)


# ------------------------------------------------------------------------ APIM as MCP gateway
class APIMGatewayTool(ManagedMCPGatewayTool):
    def _auth_headers(self, endpoint, config=None, **kwargs):
        key = resolve_config("azure_apim_subscription_key", self._cfg(config), kwargs)
        return {"Ocp-Apim-Subscription-Key": key} if key else {}


# ------------------------------------------------------------------------ substrate
class AzureObservability(ManagedObservability):
    cloud_resource = {"cloud.provider": "azure", "telemetry.backend": "application-insights (azure-monitor-otel)"}


class AzureInfraProvisioner(TerraformInfraProvisioner):
    terraform_dir = TERRAFORM

    def _define_network_topologies(self, tenant_id, availability_tier, config=None, **kwargs):
        return {"vnet": f"e2a-{tenant_id}", "zone_redundant": availability_tier != "STANDARD",
                "private_endpoints": ["openai", "search", "contentsafety", "cosmos", "keyvault"]}

    def _define_edge_security(self, networks, config=None, **kwargs):
        return {"gateway": "api-management (also the MCP gateway)", "waf": "front-door waf policy"}

    def _define_data_perimeter_substrate(self, networks, tenant_id, tenancy_model, config=None, **kwargs):
        db = f"e2a-{tenant_id}" if tenancy_model == "SILO" else "e2a"
        return {"intake": "service-bus topic intake", "tasks": "service-bus queue tasks (max delivery 3)",
                "cosmos_database": db, "containers": ["idempotency", "checkpoints"]}

    def _define_compute_tiers(self, networks, data_meta, config=None, **kwargs):
        return {"tier1": "container-apps job:ingress-validator", "tier2": "container-app:orchestration-engine",
                "tier4": "container-app:async-workers (KEDA on queue depth)"}

    def _define_iam_governance_framework(self, compute, data_meta, config=None, **kwargs):
        return {"tier1": ["Azure Service Bus Data Sender (intake)"],
                "tier2": ["Cognitive Services OpenAI User", "Search Index Data Reader", "Cognitive Services User",
                          "Cosmos DB Built-in Data Contributor"],
                "tier4": ["Azure Service Bus Data Receiver", "Search Index Data Contributor"]}

    def _managed_ai_resources(self, config):
        return {"openai_deployments": [resolve_config("model_id", config), resolve_config("embedding_model_id", config)],
                "search_index": resolve_config("vector_index_name", config), "content_safety": True}

    def _tfvars(self, manifest, config):
        ai = manifest["topology_maps"]["managed_ai"]
        return {**super()._tfvars(manifest, config), "chat_deployment": ai["openai_deployments"][0],
                "embedding_deployment": ai["openai_deployments"][1], "search_index": ai["search_index"],
                "zone_redundant": manifest["topology_maps"]["networks"]["zone_redundant"]}


class AzureReleasePipeline(ManagedReleasePipeline):
    registry = "acr"
    rollout = "container-apps revision traffic split"
