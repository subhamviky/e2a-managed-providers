"""Capability matrix: what a bound managed service can do on its platform.

The same model can expose different features on different platforms (Claude on Amazon
Bedrock has no Anthropic MCP connector; Claude Platform on AWS has). A profile lists the
features the deployment relies on (`managed_features`); Bindings.verify() refuses to
start when no bound object provides one, instead of failing on the first request.
"""
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable


class Capability(str, Enum):
    # model calls
    TOOL_USE = "tool_use"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    PROMPT_CACHING = "prompt_caching"
    REASONING_EFFORT = "reasoning_effort"
    REFUSAL_FALLBACK = "refusal_fallback"
    MCP_CONNECTOR = "mcp_connector"            # the provider's servers call remote MCP servers
    SERVER_WEB_SEARCH = "server_web_search"
    BATCH = "batch"
    # retrieval
    EMBEDDINGS = "embeddings"
    VECTOR_SEARCH = "vector_search"
    HYBRID_SEARCH = "hybrid_search"            # keyword + vector fused by the service
    MANAGED_RETRIEVAL = "managed_retrieval"    # chunking, embedding and search all managed
    RERANK = "rerank"
    # safety, prompts, runtimes
    GUARDRAILS = "guardrails"
    PROMPT_REGISTRY = "prompt_registry"
    MCP_GATEWAY = "mcp_gateway"                # a managed endpoint in front of MCP servers
    MANAGED_AGENT_RUNTIME = "managed_agent_runtime"
    # substrate
    IDEMPOTENCY_STORE = "idempotency_store"
    CHECKPOINT_STORE = "checkpoint_store"
    TELEMETRY = "telemetry"
    INFRA_AS_CODE = "infra_as_code"
    RELEASE_PIPELINE = "release_pipeline"


class CapabilityError(RuntimeError):
    """A profile requires a feature that none of its bindings provides on that platform."""


def capabilities_of(obj: Any, config: Dict[str, Any]) -> FrozenSet[str]:
    """A bound object declares `capabilities` (a set) or `capabilities_for(config)`."""
    if obj is None:
        return frozenset()
    fn = getattr(obj, "capabilities_for", None)
    if callable(fn):
        return frozenset(str(Capability(c).value) for c in fn(config))
    return frozenset(str(Capability(c).value) for c in getattr(obj, "capabilities", ()))


def missing(required: Iterable[str], provided: Iterable[str]) -> list:
    have = {Capability(c).value for c in provided}
    return sorted({Capability(c).value for c in required} - have)
