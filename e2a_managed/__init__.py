"""e2a-managed-providers: frontier-provider managed services behind the E2A contracts.

    from e2a_managed import bindings_from_profile
    bindings = bindings_from_profile("anthropic_bedrock")      # or E2A_PROVIDER_PROFILE
    config = bindings.config(tools=..., governance_engine=...)

Core (provider-neutral): config_keys, profile, bindings, capabilities, codec,
managed_llm, managed_rag, guardrails, delegated, mcp_gateway, observability, iac,
release, offline. Providers: e2a_managed.providers.<provider>. Cloud substrate shared by
providers on the same cloud: e2a_managed.clouds.<cloud>.
"""
from . import config_keys  # noqa: F401  (registers the managed configuration keys first)
from e2a.bindings import register_provider_package

from .bindings import SEAMS, Bindings, bindings_from_profile
from .capabilities import Capability, CapabilityError
from .profile import ProviderProfile, available_profiles, provider_profiles

register_provider_package(__name__)   # bindable from profiles; its profiles are found by name

__all__ = ["SEAMS", "Bindings", "bindings_from_profile", "Capability", "CapabilityError", "ProviderProfile",
           "available_profiles", "provider_profiles"]
__version__ = "0.1.0"
