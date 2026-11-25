"""Swap a project's SimulatedLLMService for a managed provider without touching the project.

    managed = managed_replay(build_o2c_llm(), "openai_azure")      # offline: real SDK, replayed wire
    managed = managed_live(build_o2c_llm(), "openai_azure")        # live: credentials from the env

The returned object IS the provider's adapter (a BaseLLMAgentService), so E2A treats it
exactly like any model. In replay mode the simulated rules supply the content behind
the provider's real wire format. Anything the project's tests read from the simulator
(`_rules`, `usage_summary()`) is forwarded to it, so existing test suites run unchanged.

The pytest plugin (e2a_managed.pytest_plugin) applies this to a project's factory
functions from configuration: E2A_PROVIDER_PROFILE + E2A_SWAP_FACTORIES.
"""
import importlib
import os
import sys
from typing import Any, Dict

from .bindings import bindings_from_profile
from .offline import offline_llm_service, simulated

_CACHE: Dict[type, type] = {}


def _forwarding(cls: type) -> type:
    if cls not in _CACHE:
        def __getattr__(self, name):
            sim = self.__dict__.get("_e2a_simulated")
            if sim is None:
                raise AttributeError(name)
            return getattr(sim, name)
        _CACHE[cls] = type(f"Replay{cls.__name__}", (cls,), {"__getattr__": __getattr__})
    return _CACHE[cls]


def managed_replay(sim_llm: Any, profile: str, **config: Any):
    service, transport = offline_llm_service(profile, simulated(sim_llm), **config)
    service.__class__ = _forwarding(type(service))
    service._e2a_simulated, service._e2a_transport = sim_llm, transport
    return service


def managed_live(sim_llm: Any, profile: str, **config: Any):
    """The profile's real adapter with credentials from the platform chain. The simulator
    is kept only for attribute forwarding (tests); it never answers."""
    service = bindings_from_profile(profile, overrides=config).llm_service
    service.__class__ = _forwarding(type(service))
    service._e2a_simulated = sim_llm
    return service


def wrap_factory(target: str, profile: str, live: bool = False) -> None:
    """Replace `package.module:factory` (returning a SimulatedLLMService) in place."""
    module_name, _, attr = target.partition(":")
    module = importlib.import_module(module_name)
    original = getattr(module, attr)
    if getattr(original, "_e2a_wrapped", False):
        return

    def factory(*args, **kwargs):
        # A project's factory may bind E2A_PROVIDER_PROFILE itself (GCP-01: the three Python services do): then, with
        # the profile set, it returns the real provider model, not the rule-based SimulatedLLMService this swap
        # wraps. Build the project's own simulated model with the profile unset, then bind the profile's transport.
        saved = os.environ.pop("E2A_PROVIDER_PROFILE", None)
        try:
            sim = original(*args, **kwargs)
        finally:
            if saved is not None:
                os.environ["E2A_PROVIDER_PROFILE"] = saved
        return (managed_live if live else managed_replay)(sim, profile)
    factory._e2a_wrapped = True
    factory.__wrapped__ = original
    setattr(module, attr, factory)
    # A project's conftest (or app module) may already hold `from ...llm import factory`, imported before this swap ran
    # (pytest loads some conftest files before any plugin's configure hook): such a name would keep the original.
    # Point every module-level alias of the original at the wrapper too.
    for other in list(sys.modules.values()):
        namespace = getattr(other, "__dict__", None)
        if not isinstance(namespace, dict) or other is module:
            continue
        for name, value in list(namespace.items()):
            if value is original:
                namespace[name] = factory
