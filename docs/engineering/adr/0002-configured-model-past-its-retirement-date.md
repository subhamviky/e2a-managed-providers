# ADR 0002: A configured model past its retirement date fails

- **Status:** Accepted
- **Date:** v0.2.0
- **Deciders:** repository owner

## Context

Provider models are retired on schedule; a stale model id would fail at runtime in production.

## Options considered

A) Discover at runtime. B) A CI step fails when a configured model is past its retirement date.

## Decision

B (the model lifecycle test).

## Consequences

Retirements surface in CI, not at 3 a.m. The catalogue needs maintenance.
