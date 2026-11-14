"""Provider profiles are E2A's `Profile` since E2A v1.6.0 (`e2a.bindings`).

A profile is one YAML file under providers/<provider>/profiles/. E2A loads it (PyYAML is a
dependency of this package), resolves ${VAR} / ${VAR:-default} placeholders, supports
`extends`, and refuses unknown fields and seams. `ProviderProfile` is kept as an alias.
"""
from pathlib import Path

from e2a.bindings import Profile, available_profiles

ProviderProfile = Profile
PROVIDERS_DIR = Path(__file__).parent / "providers"


def provider_profiles():
    """This package's managed-provider profiles (E2A's available_profiles() also lists the
    profiles of every other registered package, such as E2A's own 'simulated')."""
    return sorted({p.stem for suffix in ("yaml", "yml", "json") for p in PROVIDERS_DIR.glob(f"*/profiles/*.{suffix}")})


__all__ = ["Profile", "ProviderProfile", "available_profiles", "provider_profiles", "PROVIDERS_DIR"]
