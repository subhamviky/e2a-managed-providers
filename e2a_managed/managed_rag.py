"""RAG on managed services through E2A's v1.2 ports.

E2A splits retrieval into two shared ports (one embedding model, one index, so the
write and read sides cannot drift) plus two read-side hooks (_rerank,
_evaluate_answer). Managed services map onto them as:

    BaseEmbeddingModel  <- Titan / text-embedding-3 / gemini-embedding / Llama Stack embeddings
    BaseVectorIndex     <- S3 Vectors / Azure AI Search / Vertex Vector Search / Llama Stack vector_io
    _rerank             <- ManagedReranker: Bedrock Rerank (Cohere / Amazon), Azure semantic
                           ranker, Vertex AI Ranking API
    _evaluate_answer    <- lexical groundedness by default; providers may plug a managed check

E2A has no reranker port, so `ManagedReranker` is defined here (an upstream candidate).
`retrieve()` does not hand the query to _rerank(); _search_index() therefore tags each
hit with it, so a cross-encoder reranker can score (query, passage) pairs.
"""
import abc
import math
import re
from typing import Any, Dict, FrozenSet, List, Optional

from e2a import BaseEmbeddingModel, BaseIndexingPipeline, BaseRAGPipeline, BaseVectorIndex, RAGPipeline, \
    resolve_config

from . import config_keys  # noqa: F401
from .capabilities import Capability

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an and are as at be by can do does for from has have how i if in is it its of on or our the "
            "their this to we what when which who why will with within".split())


def content_words(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class ManagedEmbeddingModel(BaseEmbeddingModel):
    """An embedding model behind a managed API. `model_id` is resolved from config so the
    E2A check 'documents and queries use the same model' compares real model ids."""
    capabilities: FrozenSet[str] = frozenset({Capability.EMBEDDINGS})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config = dict(config or {})
        self.model_id = str(resolve_config("embedding_model_id", self.config) or self.default_model_id)
        self._client = client

    default_model_id = "unspecified"

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client(self.config)
        return self._client

    @abc.abstractmethod
    def _build_client(self, config: Dict[str, Any]) -> Any:
        """Provider SDK client."""


class ManagedVectorIndex(BaseVectorIndex):
    capabilities: FrozenSet[str] = frozenset({Capability.VECTOR_SEARCH})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config = dict(config or {})
        self.index_name = str(resolve_config("vector_index_name", self.config))
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client(self.config)
        return self._client

    @abc.abstractmethod
    def _build_client(self, config: Dict[str, Any]) -> Any:
        """Provider SDK client."""


class ManagedReranker(abc.ABC):
    capabilities: FrozenSet[str] = frozenset({Capability.RERANK})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config = dict(config or {})
        self.model_id = resolve_config("rerank_model_id", self.config)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client(self.config)
        return self._client

    @abc.abstractmethod
    def _build_client(self, config: Dict[str, Any]) -> Any:
        """Provider SDK client."""

    @abc.abstractmethod
    def rerank(self, query: str, hits: List[dict], top_n: int) -> List[dict]:
        """Return the top_n hits, best first, each with a `rerank_score`."""


class ManagedRAGPipeline(BaseRAGPipeline):
    """Read side over the managed ports. A project subclasses it only to change the
    groundedness rule or the citation format."""
    namespace_prefix = "rag"

    def __init__(self, config: Optional[Dict[str, Any]] = None, embedding_model: BaseEmbeddingModel = None,
                 vector_index: BaseVectorIndex = None, reranker: Optional[ManagedReranker] = None,
                 namespace_prefix: Optional[str] = None) -> None:
        self.config = dict(config or {})
        self.embedding_model, self.vector_index, self.reranker = embedding_model, vector_index, reranker
        if namespace_prefix:
            self.namespace_prefix = namespace_prefix

    def _search_index(self, query_text, query_vector, config=None, tenant_id=None, **kwargs):
        hits = super()._search_index(query_text, query_vector, config, tenant_id=tenant_id, **kwargs)
        return [dict(h, _query=query_text) for h in hits]

    def _rerank(self, results, config=None, **kwargs):
        if not results:
            return []
        top_n = int(kwargs.get("rerank_top_n", 3))
        query = results[0].get("_query", "")
        clean = [{k: v for k, v in r.items() if k != "_query"} for r in results]
        if self.reranker is not None:
            return self.reranker.rerank(query, clean, top_n)
        return sorted(clean, key=lambda r: -float(r.get("score", 0.0)))[:top_n]

    def _evaluate_answer(self, answer, config=None, **kwargs) -> float:
        """Provider-neutral lexical groundedness; an adapter may replace it with a managed
        grounding check (Bedrock contextual grounding, Azure groundedness detection)."""
        return lexical_groundedness(answer, kwargs.get("retrieved", []))


def lexical_groundedness(answer: str, retrieved: List[dict]) -> float:
    """Share of answer sentences whose content words are >= 60% present in the passages."""
    evidence = set().union(*(content_words(r.get("text", "")) for r in retrieved)) if retrieved else set()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", (answer or "").strip()) if s]
    if not sentences or not evidence:
        return 0.0
    grounded = sum(1 for s in sentences if (w := content_words(s)) and len(w & evidence) / len(w) >= 0.6)
    return grounded / len(sentences)


class ParagraphIndexingPipeline(BaseIndexingPipeline):
    """Write side: one chunk per paragraph, chunk id = '<doc>#<n>', upserted idempotently."""
    namespace_prefix = "rag"

    def __init__(self, embedding_model: BaseEmbeddingModel, vector_index: BaseVectorIndex,
                 namespace_prefix: Optional[str] = None) -> None:
        self.embedding_model, self.vector_index = embedding_model, vector_index
        if namespace_prefix:
            self.namespace_prefix = namespace_prefix

    def _chunk_documents(self, document, config=None, **kwargs):
        paragraphs = [p.strip() for p in document["text"].split("\n\n") if p.strip()]
        if not paragraphs:
            raise ValueError(f"{document['id']}: empty document")
        return [{"text": p, "metadata": {"chunk_id": f"{document['id']}#{i}", "doc_id": document["id"]}}
                for i, p in enumerate(paragraphs)]

    def _handle_error(self, error, document, config=None, **kwargs):
        pass                                    # the base already lists the document in failed_keys


def managed_rag(bindings, namespace_prefix: str = "rag") -> RAGPipeline:
    """Compose E2A's RAGPipeline facade from a profile's bound ports."""
    model, index = bindings.embedding_model, bindings.vector_index
    read = bindings.rag_pipeline or ManagedRAGPipeline(bindings.config(), model, index, bindings.reranker,
                                                       namespace_prefix)
    return RAGPipeline(ParagraphIndexingPipeline(model, index, namespace_prefix), read)
