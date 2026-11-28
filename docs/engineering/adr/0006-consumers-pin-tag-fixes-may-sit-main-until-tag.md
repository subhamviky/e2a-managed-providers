# ADR 0006: Consumers pin a tag; fixes may sit on `main` until a tag exists

- **Status:** Accepted
- **Date:** v0.2.0
- **Deciders:** repository owner

## Context

The assistant needed the Gemma fix before a tag was cut.

## Options considered

A) Consume `main`. B) Pin tags; use `pip install ...@main` temporarily when a fix is needed, then tag.

## Decision

B.

## Consequences

Reproducible builds with a documented, temporary exception.
