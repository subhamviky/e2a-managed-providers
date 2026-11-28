# ADR 0001: Frontier-provider managed services behind E2A's vendor-neutral contracts, chosen by profile

- **Status:** Accepted
- **Date:** v0.1
- **Deciders:** repository owner

## Context

Projects should not depend on one provider's SDK, and E2A's core must name no vendor.

## Options considered

A) Provider code in each project. B) A separate repository implementing E2A's contracts (models, embeddings, vector search, rerankers, guardrails, delegated runtimes) for Anthropic, OpenAI, Google and Meta, chosen by configuration with profiles that extend E2A's cloud profiles.

## Decision

B. Design documents record, per provider, which managed services replace, extend or leave to E2A.

## Consequences

A provider change is a configuration change; the neutrality test in E2A keeps the core vendor-free.
