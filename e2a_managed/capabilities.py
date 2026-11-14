"""Capabilities: E2A's (`e2a.capabilities`, since E2A v1.6.0), plus this package's enum of names.

The registry, the checks and CapabilityError are E2A's own. `Capability` remains here as a
convenience for the provider classes (`Capability.TOOL_USE`); every name in it is registered
in E2A's registry, so a profile's `features` and a class's `capabilities` are checked by E2A.
"""
from enum import Enum

from e2a.capabilities import CAPABILITIES, CapabilityError, capabilities_of, missing, register_capability

__all__ = ["Capability", "CapabilityError", "capabilities_of", "missing"]


class Capability(str, Enum):
    # model calls
    TOOL_USE = "tool_use"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    PROMPT_CACHING = "prompt_caching"
    REASONING_EFFORT = "reasoning_effort"
    REFUSAL_FALLBACK = "refusal_fallback"
    REFUSAL_FALLBACK_SERVER = "refusal_fallback_server"   # the provider falls back itself: E2A takes no route
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


for _c in Capability:                       # idempotent: E2A already knows the common ones
    if _c.value not in CAPABILITIES:
        register_capability(_c.value)
