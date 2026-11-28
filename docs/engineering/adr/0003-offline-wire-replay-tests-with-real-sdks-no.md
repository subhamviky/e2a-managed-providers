# ADR 0003: Offline wire-replay tests with real SDKs; no provider accounts in CI

- **Status:** Accepted
- **Date:** v0.1
- **Deciders:** repository owner

## Context

CI must not need accounts, keys or money.

## Options considered

A) Live calls. B) Replay recorded wire exchanges against the real SDKs.

## Decision

B (all providers).

## Consequences

Fast, free and deterministic tests that still exercise the real SDKs. A provider's behaviour change is seen only when the recordings are refreshed.
