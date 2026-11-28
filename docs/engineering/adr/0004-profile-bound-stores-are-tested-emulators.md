# ADR 0004: Profile-bound stores are tested on the emulators, races included

- **Status:** Accepted
- **Date:** v0.1
- **Deciders:** repository owner

## Context

A profile binds E2A's state ports to a cloud's store; the contract must hold with real semantics.

## Options considered

A) Mocks. B) DynamoDB Local and the Firestore and Cosmos DB emulators run in CI for the stores' contract.

## Decision

B.

## Consequences

Real conditional-write semantics are exercised.
