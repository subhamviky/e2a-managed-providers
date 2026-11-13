"""Provider profiles: one YAML file per managed-provider combination.

A profile names the class bound to each E2A seam and supplies the config-dict layer of
E2A's resolution order. It never holds secrets or environment-specific values directly:
those are written as ${ENV_VAR} or ${ENV_VAR:-default}. A placeholder whose variable is
unset and has no default is dropped, so the key falls through to E2A's own environment
variable and registry default (the framework's resolution stays the single authority).

    name: anthropic_bedrock
    provider: anthropic
    cloud: aws
    features: [tool_use, prompt_caching, guardrails]
    bindings:
      llm_service: e2a_managed.providers.anthropic_aws.llm_service:BedrockClaudeLLMService
    config:
      model_id: ${LLM_MODEL_ID:-anthropic.claude-opus-5-5}
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

PROVIDERS_DIR = Path(__file__).parent / "providers"
_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")
_DROP = object()


def _interpolate(value: Any) -> Any:
    if isinstance(value, dict):
        out = {k: _interpolate(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not _DROP}
    if isinstance(value, list):
        return [v for v in (_interpolate(v) for v in value) if v is not _DROP]
    if not isinstance(value, str):
        return value
    whole = _PLACEHOLDER.fullmatch(value.strip())
    if whole:
        env, default = whole.group(1), whole.group(2)
        resolved = os.environ.get(env, default)
        if resolved is None:
            return _DROP
        return yaml.safe_load(resolved) if resolved != "" else resolved   # "8" -> 8, "true" -> True
    return _PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    provider: str
    cloud: str
    description: str = ""
    features: List[str] = field(default_factory=list)
    bindings: Dict[str, str] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: Optional[str] = None) -> "ProviderProfile":
        unknown = set(data) - {"name", "provider", "cloud", "description", "features", "bindings", "config"}
        if unknown:
            raise ValueError(f"profile {source or data.get('name')}: unknown fields {sorted(unknown)}")
        return cls(name=data["name"], provider=data["provider"], cloud=data["cloud"],
                   description=data.get("description", ""), features=list(data.get("features") or []),
                   bindings=dict(data.get("bindings") or {}),
                   config=_interpolate(dict(data.get("config") or {})), source=source)

    @classmethod
    def load(cls, name_or_path: str) -> "ProviderProfile":
        """By path, or by name: providers/<provider>/profiles/<name>.yaml."""
        path = Path(name_or_path)
        if not path.suffix:
            hits = sorted(PROVIDERS_DIR.glob(f"*/profiles/{name_or_path}.yaml"))
            if not hits:
                raise FileNotFoundError(f"no provider profile named '{name_or_path}' "
                                        f"(available: {', '.join(available_profiles())})")
            path = hits[0]
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.from_dict(data, source=str(path))


def available_profiles() -> List[str]:
    return sorted(p.stem for p in PROVIDERS_DIR.glob("*/profiles/*.yaml"))
