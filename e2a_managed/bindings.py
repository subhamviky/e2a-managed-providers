"""The composition root: bind every E2A seam from a provider profile.

This is the one place a project changes to stop hard-wiring SimulatedLLMService:

    bindings = bindings_from_profile()          # E2A_PROVIDER_PROFILE=anthropic_bedrock
    service = AssistantService(..., llm=bindings.llm_service)
    workflow.execute(state, config=bindings.config(tools=..., governance_engine=...))

Switching provider is then configuration only: another profile name and that
provider's environment variables. Nothing here is specific to a provider or a project,
which is why this module is the candidate to move upstream into E2A itself
(docs/UPSTREAM.md).

Binding rules
  * a profile maps seam -> "module:Class"; a factory may be passed instead (tests, runners)
  * every class is built as Class(config=<profile config>), lazily on first access, so a
    deployment installs only the SDKs of the seams it actually uses
  * overrides passed to bindings_from_profile() win over the profile (same order as E2A:
    the caller's explicit value is the most specific)
"""
import importlib
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Union

from e2a import resolve_config

from . import config_keys  # noqa: F401  (registers the managed keys)
from .capabilities import CapabilityError, capabilities_of, missing
from .profile import ProviderProfile

SEAMS = (
    "llm_service",           # BaseLLMAgentService            -> config['llm_service']
    "embedding_model",       # BaseEmbeddingModel
    "vector_index",          # BaseVectorIndex
    "reranker",              # ManagedReranker (used by ManagedRAGPipeline._rerank)
    "rag_pipeline",          # BaseRAGPipeline (fully managed retrieval, optional)
    "guardrail_stage",       # IngressStage                    -> governance engine stages
    "prompt_registry",       # BasePromptRegistry
    "mcp_gateway_tool",      # MCPToolService through a managed gateway
    "delegated_runtime",     # managed agent runtime            -> DelegatedAgent
    "observability_engine",  # BaseObservability               -> config['observability_engine']
    "idempotency_store",     # BaseIdempotencyStore
    "checkpoint_store",      # BaseCheckpointStore
    "infra_provisioner",     # BaseInfraProvisioner (CI/CD time, not request time)
    "release_pipeline",      # BasePipeline (CI/CD time)
)
_CONFIG_SEAMS = ("llm_service", "observability_engine")   # seams E2A reads from the config dict
Factory = Union[str, Callable[..., Any]]


def _import(target: str) -> Callable[..., Any]:
    module, _, attr = target.partition(":")
    if not attr:
        raise ValueError(f"binding '{target}' must be 'package.module:ClassName'")
    return getattr(importlib.import_module(module), attr)


class Bindings:
    """Lazily built seam objects plus the profile's config layer."""

    def __init__(self, profile: ProviderProfile, factories: Mapping[str, Factory],
                 overrides: Optional[Mapping[str, Any]] = None) -> None:
        unknown = set(factories) - set(SEAMS)
        if unknown:
            raise ValueError(f"profile {profile.name}: unknown seams {sorted(unknown)}")
        self.profile = profile
        self._factories = dict(factories)
        self._config: Dict[str, Any] = {**profile.config, **{k: v for k, v in (overrides or {}).items()
                                                              if k not in SEAMS}}
        self._built: Dict[str, Any] = {k: v for k, v in (overrides or {}).items() if k in SEAMS}

    # -- seams
    def get(self, seam: str) -> Any:
        if seam not in SEAMS:
            raise KeyError(seam)
        if seam not in self._built:
            factory = self._factories.get(seam)
            if factory is None:
                self._built[seam] = None
            else:
                cls = _import(factory) if isinstance(factory, str) else factory
                self._built[seam] = cls(config=dict(self._config))
        return self._built[seam]

    def __getattr__(self, seam: str) -> Any:
        if seam in SEAMS:
            return self.get(seam)
        raise AttributeError(seam)

    def bound(self) -> Iterable[str]:
        return [s for s in SEAMS if s in self._factories or self._built.get(s) is not None]

    # -- config for the E2A entry points
    def config(self, **extra: Any) -> Dict[str, Any]:
        """The config dict for BaseWorkflow.execute() / BaseAgent.run(): the profile's keys
        plus the engines E2A reads from config. `extra` (tools, governance_engine, the
        project's own settings) is added last."""
        cfg = dict(self._config)
        for seam in _CONFIG_SEAMS:
            obj = self.get(seam)
            if obj is not None:
                cfg[seam] = obj
        cfg.update(extra)
        return cfg

    # -- startup check
    def verify(self, required: Optional[Iterable[str]] = None) -> Dict[str, list]:
        """Build every bound seam and check the required features against what the bound
        objects declare. Raises CapabilityError listing what is missing."""
        required = list(required if required is not None else
                        (self.profile.features or resolve_config("managed_features", self._config)))
        provided: Dict[str, list] = {}
        for seam in self.bound():
            caps = capabilities_of(self.get(seam), self._config)
            if caps:
                provided[seam] = sorted(caps)
        gaps = missing(required, [c for caps in provided.values() for c in caps])
        if gaps:
            raise CapabilityError(f"profile '{self.profile.name}' requires {gaps}, which none of its "
                                  f"bindings provides on {self.profile.provider}/{self.profile.cloud}")
        return provided


def bindings_from_profile(profile: Union[None, str, ProviderProfile] = None, *,
                          overrides: Optional[Mapping[str, Any]] = None,
                          factories: Optional[Mapping[str, Factory]] = None,
                          verify: bool = False) -> Bindings:
    """profile: a ProviderProfile, a name/path, or None -> E2A_PROVIDER_PROFILE.
    overrides: config keys and/or already-built seam objects (e.g. an injected client).
    factories: replace a profile binding (e.g. a project-specific subclass)."""
    if not isinstance(profile, ProviderProfile):
        name = profile or resolve_config("provider_profile", dict(overrides or {}))
        if not name:
            raise ValueError("no provider profile: pass one or set E2A_PROVIDER_PROFILE")
        profile = ProviderProfile.load(name)
    bindings = Bindings(profile, {**profile.bindings, **(factories or {})}, overrides)
    if verify:
        bindings.verify()
    return bindings
