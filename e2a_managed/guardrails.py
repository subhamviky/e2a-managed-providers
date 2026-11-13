"""Managed guardrails as E2A governance stages.

E2A v1.2 governance is an ordered list of stages with typed refusals. A managed
guardrail (Bedrock Guardrails, Azure AI Content Safety Prompt Shields, Google Model
Armor, Llama Guard via Llama Stack shields) becomes one more IngressStage, placed next
to E2A's own FirewallStage: the pattern firewall stays as a cheap first screen, the
managed classifier adds model-based screening. A refusal is E2A's FirewallRefusal, so
callers and tests see the same type whichever provider screened the request.

A provider error fails CLOSED by default (`fail_open=False`): a guardrail that cannot
answer must not let an unscreened request through.
"""
import abc
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional

from e2a import FirewallRefusal, IngressStage, resolve_config

from . import config_keys  # noqa: F401
from .capabilities import Capability


@dataclass
class Verdict:
    allowed: bool
    categories: List[str] = field(default_factory=list)
    sanitized: Optional[str] = None          # provider-masked text (PII), when the service returns one
    detail: str = ""


class ManagedGuardrailStage(IngressStage):
    name = "managed_guardrail"
    phase = "screen"
    capabilities: FrozenSet[str] = frozenset({Capability.GUARDRAILS})

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None, fail_open: bool = False) -> None:
        self.config = dict(config or {})
        self.fields = list(resolve_config("guardrail_fields", self.config))
        self.fail_open = fail_open
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client(self.config)
        return self._client

    @abc.abstractmethod
    def _build_client(self, config: Dict[str, Any]) -> Any:
        """Provider SDK / HTTP client."""

    @abc.abstractmethod
    def _screen(self, text: str, ctx: Any) -> Verdict:
        """Ask the managed guardrail about one input text."""

    def check(self, state: Dict[str, Any], ctx, messages) -> Dict[str, Any]:
        for name in self.fields:
            value = state.get(name)
            if not isinstance(value, str) or not value:
                continue
            try:
                verdict = self._screen(value, ctx)
            except Exception as e:                       # provider down / throttled
                messages.warn("guardrail_unavailable", stage=self.name, error=type(e).__name__)
                if self.fail_open:
                    continue
                raise FirewallRefusal(f"{self.name} unavailable; request not screened", self.name) from e
            if not verdict.allowed:
                messages.warn("guardrail_blocked", stage=self.name, categories=verdict.categories)
                raise FirewallRefusal(f"request refused by {self.name}"
                                      f"{' (' + ', '.join(verdict.categories) + ')' if verdict.categories else ''}",
                                      self.name)
            if verdict.sanitized is not None and verdict.sanitized != value:
                state[name] = verdict.sanitized
                messages.info("guardrail_masked", stage=self.name, field=name)
        return state
