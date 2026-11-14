"""Google Cloud platform services shared by every model provider on GCP.

    VertexVectorSearchIndex     BaseVectorIndex   Vertex AI Vector Search, hybrid (dense + sparse)
    VertexRankingReranker       ManagedReranker   Vertex AI Ranking API (semantic-ranker)
    ModelArmorStage             IngressStage      Model Armor sanitizeUserPrompt (PI/jailbreak, RAI, SDP)
    VertexAgentEngineRuntime    DelegatedRuntime  an ADK agent on Vertex AI Agent Engine
    IAPGatewayTool              MCPToolService    MCP server on Cloud Run behind IAP / Apigee (ID token)
    FirestoreIdempotencyStore /
    FirestoreCheckpointStore                      e2a-adapters (E2A v1.5), bound from config
    GCPObservability, GCPManagedInfraProvisioner, GCPReleasePipeline

REST calls carry an ADC bearer token; the httpx transport is injectable for tests.
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

import httpx
from e2a import GCPInfraProvisioner, resolve_config

from ...capabilities import Capability as C
from ...codec import result
from ...delegated import DelegatedRuntime
from ...guardrails import ManagedGuardrailStage, Verdict
from ...iac import TerraformInfraProvisioner
from ...managed_rag import ManagedReranker, ManagedVectorIndex, content_words
from ...mcp_gateway import ManagedMCPGatewayTool
from ...observability import ManagedObservability
from ...release import ManagedReleasePipeline
from ...stores import AdapterCheckpointStore, AdapterIdempotencyStore

TERRAFORM = Path(__file__).parent / "terraform"
SPARSE_DIM = 2 ** 20


def adc_token() -> str:
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


class _GoogleRest:
    def __init__(self, config: Optional[Dict[str, Any]] = None, transport: Any = None,
                 token: Optional[str] = None) -> None:
        self.config, self.transport, self._token = dict(config or {}), transport, token

    def _post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(transport=self.transport, timeout=30.0) as http:
            resp = http.post(url, json=body, headers={"Authorization": f"Bearer {self._token or adc_token()}"})
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    @property
    def location(self) -> str:
        return resolve_config("gcp_location", self.config)

    @property
    def project(self) -> str:
        return resolve_config("gcp_project", self.config)


def sparse_vector(text: str) -> Dict[str, List]:
    """Term-frequency sparse embedding (hashed terms) for Vector Search's hybrid query:
    the keyword leg of E2A's hybrid contract, fused with the dense leg by the service."""
    counts: Dict[int, float] = {}
    for w in content_words(text):
        idx = int(hashlib.sha1(w.encode()).hexdigest(), 16) % SPARSE_DIM
        counts[idx] = counts.get(idx, 0.0) + 1.0
    return {"dimensions": list(counts), "values": list(counts.values())}


# ------------------------------------------------------------------------ Vector Search
class VertexVectorSearchIndex(_GoogleRest, ManagedVectorIndex):
    """Streaming-update index (upsertDatapoints/removeDatapoints) + public index endpoint
    (findNeighbors). Tenants are a `namespace` restrict on every datapoint; the passage
    text and metadata live in `embedding_metadata` (returned with the neighbours)."""
    capabilities: FrozenSet[str] = frozenset({C.VECTOR_SEARCH, C.HYBRID_SEARCH})

    def __init__(self, config=None, transport=None, token=None, client=None):
        _GoogleRest.__init__(self, config, transport, token)
        ManagedVectorIndex.__init__(self, config, client)
        self._written: Dict[tuple, set] = {}

    def _build_client(self, config):
        return None

    def _api(self, resource: str, verb: str) -> str:
        return f"https://{self.location}-aiplatform.googleapis.com/v1/{resource}:{verb}"

    def upsert(self, namespace, chunks, vectors):
        points = [{"datapoint_id": f"{namespace}/{c['metadata']['chunk_id']}", "feature_vector": list(v),
                   "sparse_embedding": sparse_vector(c["text"]),
                   "restricts": [{"namespace": "namespace", "allow_list": [namespace]},
                                 {"namespace": "doc_id", "allow_list": [str(c["metadata"].get("doc_id"))]}],
                   "embedding_metadata": {**c["metadata"], "text": c["text"]}}
                  for c, v in zip(chunks, vectors)]
        self._post(self._api(resolve_config("vertex_index", self.config), "upsertDatapoints"), {"datapoints": points})
        for c in chunks:
            self._written.setdefault((namespace, str(c["metadata"].get("doc_id"))), set()).add(
                f"{namespace}/{c['metadata']['chunk_id']}")

    def delete(self, namespace, doc_id):
        """removeDatapoints takes ids only. Ids written by this process are remembered; for
        documents indexed elsewhere the paragraph-chunker ids '<doc>#0..N' are removed
        (removing an absent id is a no-op). A project with its own chunk ids passes them
        in config['vertex_delete_ids'] or re-ingests into a fresh index."""
        ids = sorted(self._written.get((namespace, doc_id), set())) or \
            [f"{namespace}/{doc_id}#{i}" for i in range(int((self.config.get("profile_settings") or {}).get("vertex_max_chunks_per_doc") or self.config.get("vertex_max_chunks_per_doc") or 256))]
        for i in range(0, len(ids), 1000):
            self._post(self._api(resolve_config("vertex_index", self.config), "removeDatapoints"),
                       {"datapoint_ids": ids[i:i + 1000]})
        self._written.pop((namespace, doc_id), None)

    def search(self, namespace, query_text, query_vector, top_k):
        body = {"deployed_index_id": resolve_config("vertex_deployed_index_id", self.config),
                "return_full_datapoint": True,
                "queries": [{"neighbor_count": int(top_k), "rrf": {"alpha": 0.5},
                             "datapoint": {"feature_vector": list(query_vector),
                                           "sparse_embedding": sparse_vector(query_text),
                                           "restricts": [{"namespace": "namespace", "allow_list": [namespace]}]}}]}
        resp = self._post(self._api(resolve_config("vertex_index_endpoint", self.config), "findNeighbors"), body)
        out = []
        for n in (resp.get("nearestNeighbors") or [{}])[0].get("neighbors", []):
            meta = dict((n.get("datapoint") or {}).get("embeddingMetadata") or {})
            out.append({**meta, "chunk_id": meta.get("chunk_id") or n["datapoint"]["datapointId"],
                        "text": meta.pop("text", ""), "score": float(n.get("distance", 0.0)),
                        "sparse": n.get("sparseDistance")})
        return out


# ------------------------------------------------------------------------ Ranking API
class VertexRankingReranker(_GoogleRest, ManagedReranker):
    default_model = "semantic-ranker-default@latest"

    def __init__(self, config=None, transport=None, token=None, client=None):
        _GoogleRest.__init__(self, config, transport, token)
        ManagedReranker.__init__(self, config, client)

    def _build_client(self, config):
        return None

    def rerank(self, query, hits, top_n):
        if not hits:
            return []
        name = (f"projects/{self.project}/locations/global/rankingConfigs/"
                f"{resolve_config('vertex_ranking_config', self.config)}")
        resp = self._post(f"https://discoveryengine.googleapis.com/v1/{name}:rank",
                          {"model": self.model_id or self.default_model, "query": query, "topN": int(top_n),
                           "records": [{"id": str(i), "content": h["text"]} for i, h in enumerate(hits)]})
        return [dict(hits[int(r["id"])], rerank_score=float(r.get("score", 0.0))) for r in resp.get("records", [])]


# ------------------------------------------------------------------------ Model Armor
class ModelArmorStage(_GoogleRest, ManagedGuardrailStage):
    """sanitizeUserPrompt against a Model Armor template (prompt injection & jailbreak,
    responsible-AI filters, malicious URLs, Sensitive Data Protection). A MATCH_FOUND
    filter result refuses; SDP de-identification returns the masked text."""
    name = "model_armor"

    def __init__(self, config=None, client=None, transport=None, token=None, fail_open=False):
        _GoogleRest.__init__(self, config, transport, token)
        ManagedGuardrailStage.__init__(self, config, client, fail_open)

    def _build_client(self, config):
        return None

    def _screen(self, text, ctx):
        template = resolve_config("model_armor_template", self.config)
        location = re.search(r"/locations/([^/]+)/", template).group(1)
        resp = self._post(f"https://modelarmor.{location}.rep.googleapis.com/v1/{template}:sanitizeUserPrompt",
                          {"userPromptData": {"text": text}})
        res = resp.get("sanitizationResult") or {}
        if res.get("filterMatchState") != "MATCH_FOUND":
            return Verdict(True)
        hits = [name for name, fr in (res.get("filterResults") or {}).items()
                if "MATCH_FOUND" in json.dumps(fr) and name != "sdp"]
        if hits:
            return Verdict(False, sorted(hits))
        deid = (((res.get("filterResults") or {}).get("sdp") or {}).get("sdpFilterResult") or {}) \
            .get("deidentifyResult", {}).get("data", {}).get("text")
        return Verdict(True, sanitized=deid)


# ------------------------------------------------------------------------ Agent Engine
class VertexAgentEngineRuntime(_GoogleRest, DelegatedRuntime):
    """An ADK agent deployed on Vertex AI Agent Engine, queried with :query. Its tools are
    MCP tools reached through IAP/Apigee (read targets only), so no host-side loop.
    Contract with the deployed agent's `query` method: input {"messages", "tenant_id"}
    -> output {"text", "usage"}."""
    provider = "vertex_agent_engine"

    def __init__(self, config=None, client=None, tools=None, transport=None, token=None, **kwargs):
        DelegatedRuntime.__init__(self, config, client, tools, **kwargs)
        _GoogleRest.__init__(self, config, transport, token)

    def _build_client(self, config, kwargs):
        return None

    async def _run_session(self, messages, config, tenant_id, kwargs):
        import asyncio
        engine = self._resolve_param("vertex_agent_engine", {**self._config, **(config or {})}, kwargs)
        location = re.search(r"/locations/([^/]+)/", engine).group(1)
        resp = await asyncio.to_thread(self._post, f"https://{location}-aiplatform.googleapis.com/v1/{engine}:query",
                                       {"class_method": "query",
                                        "input": {"messages": messages, "tenant_id": tenant_id}})
        out = resp.get("output") or {}
        usage = out.get("usage") or {}
        return result(out.get("text", ""), input_tokens=usage.get("input_tokens", 0),
                      output_tokens=usage.get("output_tokens", 0), stop_reason="end_turn")


class IAPGatewayTool(ManagedMCPGatewayTool):
    """An MCP server on Cloud Run behind IAP (or Apigee): a Google-signed ID token for the
    service's audience; kwargs['gateway_token'] overrides per call."""

    def _auth_headers(self, endpoint, config, kwargs):
        token = kwargs.get("gateway_token")
        if not token:
            import google.auth.transport.requests
            import google.oauth2.id_token
            token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(),
                                                          endpoint.split("/mcp")[0])
        return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------------ Firestore stores
def _firestore_adapter(store, kind: str, collection: str):
    """The e2a-adapters Firestore store on <firestore_database>, collection `collection`
    (a TTL policy on its `ttl` field: terraform/main.tf)."""
    import e2a_adapters.gcp as adapters
    client = store._injected.get("client")
    if client is None:
        from google.cloud import firestore
        client = firestore.Client(project=resolve_config("gcp_project", store.config),
                                  database=resolve_config("firestore_database", store.config))
    return getattr(adapters, kind)(client, collection, clock=store._clock)


class FirestoreIdempotencyStore(AdapterIdempotencyStore):
    """E2A claims on Firestore: e2a-adapters' FirestoreIdempotencyStore, bound from config."""

    def _build(self):
        return _firestore_adapter(self, "FirestoreIdempotencyStore", "e2a_idempotency")


class FirestoreCheckpointStore(AdapterCheckpointStore):
    """Approval checkpoints on Firestore: e2a-adapters' FirestoreCheckpointStore, bound from config."""

    def _build(self):
        return _firestore_adapter(self, "FirestoreCheckpointStore", "e2a_checkpoints")


# ------------------------------------------------------------------------ substrate
class GCPObservability(ManagedObservability):
    cloud_resource = {"cloud.provider": "gcp", "telemetry.backend": "cloud-trace + cloud-logging (OTLP)"}


class GCPManagedInfraProvisioner(TerraformInfraProvisioner, GCPInfraProvisioner):
    """Inherits E2A's GCP landing-zone reference (network, edge, perimeter, compute, IAM per
    tier, GCP Workflows saga) and adds the managed AI resources and the Terraform output."""
    terraform_dir = TERRAFORM

    def _define_edge_security(self, networks, config=None, **kwargs):
        return {**super()._define_edge_security(networks, config, **kwargs), "mcp_gateway": "iap on cloud run"}

    def _define_iam_governance_framework(self, compute, data_meta, config=None, **kwargs):
        iam = super()._define_iam_governance_framework(compute, data_meta, config, **kwargs)
        iam["tier2_role"] += ",roles/aiplatform.user,roles/modelarmor.user,roles/discoveryengine.viewer"
        return iam

    def _managed_ai_resources(self, config):
        return {"model_id": resolve_config("model_id", config), "vector_index": resolve_config("vector_index_name", config),
                "embedding_dimensions": resolve_config("embedding_dimensions", config), "model_armor_template": True}

    def _tfvars(self, manifest, config):
        ai = manifest["topology_maps"]["managed_ai"]
        return {**super()._tfvars(manifest, config), "project_id": resolve_config("gcp_project", config),
                "region": resolve_config("gcp_location", config), "vector_index_name": ai["vector_index"],
                "embedding_dimensions": ai["embedding_dimensions"] or 768}


class GCPReleasePipeline(ManagedReleasePipeline):
    registry = "artifact-registry"
    rollout = "cloud run traffic split (canary)"
