"""The composition root is E2A's own since E2A v1.6.0 (`e2a.bindings`).

This module re-exports it for existing imports. This package is registered as an E2A provider
package (entry point `e2a.providers` in pyproject.toml, and register_provider_package in
e2a_managed/__init__.py), so its classes may be bound from profiles and its profiles
(providers/*/profiles/*.yaml) are found by name.

E2A's rules apply: only registered packages may be bound; a profile's values are a
configuration layer below the environment (kwargs > config > env > profile > default);
Bindings.verify() checks the profile's `features` (or `required_capabilities`).
"""
from e2a.bindings import SEAMS, Bindings, bindings_from_profile

__all__ = ["SEAMS", "Bindings", "bindings_from_profile"]
