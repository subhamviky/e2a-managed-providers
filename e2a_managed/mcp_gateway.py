"""MCP through a managed gateway.

Real deployments rarely let an agent reach MCP servers directly. A managed gateway sits
in front of them and adds identity, authorization, rate limits and audit:

    AWS     Bedrock AgentCore Gateway (OAuth/JWT inbound, IAM outbound)
    Azure   API Management MCP server support (subscription key / Entra ID)
    GCP     Apigee or Cloud Run + IAP (Google ID token)
    Meta    Llama Stack tool runtime (MCP provider) behind the platform's ingress

E2A's MCPToolService already speaks MCP Streamable HTTP (JSON-RPC tools/call over
POST) and reuses BaseToolService's resilience, commit gate and typed outcomes. Only
two things are gateway specific: the endpoint and the auth header. Both are hooks here.

Direction 2, exposing the projects' own tools: their BaseMCPServer subclasses stay as
they are; the gateway is IaC that targets the service's /mcp route (see each cloud's
terraform). Nothing in the server changes.
"""
import abc
import itertools
from typing import Any, Dict, FrozenSet, Optional

import httpx
from e2a import MCPToolService, PhysicalOutcome

from . import config_keys  # noqa: F401
from .capabilities import Capability

_ids = itertools.count(1)


class ManagedMCPGatewayTool(MCPToolService):
    """One MCP tool reached through a managed gateway. material=False by default (reads)."""
    capabilities: FrozenSet[str] = frozenset({Capability.MCP_GATEWAY})
    gateway_url_key = "mcp_server_url"

    def __init__(self, config: Optional[Dict[str, Any]] = None, tool_name: Optional[str] = None,
                 material: bool = False, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.config = dict(config or {})
        self.tool_name, self.material, self.transport = tool_name, material, transport

    def _get_endpoint(self, config=None, tenant_id=None, **kwargs):
        from e2a import resolve_config
        return resolve_config(self.gateway_url_key, {**self.config, **(config or {})}, kwargs)

    def _build_payload(self, payload, config=None, **kwargs):
        body = super()._build_payload(payload, config, mcp_tool_name=kwargs.get("mcp_tool_name", self.tool_name),
                                      **{k: v for k, v in kwargs.items() if k != "mcp_tool_name"})
        body["id"] = next(_ids)
        return body

    @abc.abstractmethod
    def _auth_headers(self, endpoint: str, config: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, str]:
        """Gateway credentials (OAuth bearer, SigV4, subscription key, ID token)."""

    async def _send_request(self, endpoint, body, token=None, timeout=None, config=None, **kwargs):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **self._auth_headers(endpoint, {**self.config, **(config or {})}, kwargs)}
        if kwargs.get("tenant_id"):
            headers["X-Tenant-Id"] = kwargs["tenant_id"]
        if kwargs.get("idempotency_key"):
            headers["Idempotency-Key"] = kwargs["idempotency_key"]
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as client:
            resp = await client.post(endpoint, json=body, headers=headers)
        data = resp.json() if resp.content else {}
        if "error" in data or resp.status_code >= 400:
            definitive = 400 <= resp.status_code < 500 or "error" in data
            return {"status": resp.status_code, "physical_success": False, "error": data.get("error"),
                    "outcome": PhysicalOutcome.NOT_COMMITTED if definitive else PhysicalOutcome.UNKNOWN}
        result = data.get("result", {})
        return {"status": resp.status_code, "physical_success": not result.get("isError", False),
                "result": result}


class BearerGatewayTool(ManagedMCPGatewayTool):
    """OAuth/JWT bearer (AgentCore Gateway inbound auth, Apigee, Llama Stack ingress).
    The token comes from kwargs['gateway_token'] (per call) or config/env `auth_token`."""

    def _auth_headers(self, endpoint, config, kwargs):
        from e2a import resolve_config
        token = kwargs.get("gateway_token") or resolve_config("auth_token", config, kwargs)
        return {"Authorization": f"Bearer {token}"} if token else {}
