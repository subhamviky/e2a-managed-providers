# ADR 0005: A `gemini_api` profile for the Gemini Developer API's free tier

- **Status:** Accepted
- **Date:** 2026-11 (GCP-01)
- **Deciders:** repository owner

## Context

The Google Cloud landscape runs on a free key, not Vertex.

## Options considered

A) Vertex only. B) A profile for the Gemini Developer API with the adapter's features limited to what the free tier supports.

## Decision

B. A Gemma model rejects a thinking level; the fix (do not send it) is on `main`, and the assistant pins tag v0.2.0 until a new tag exists.

## Consequences

Free-tier models are usable through configuration; a tag containing the Gemma fix is still to be created (open item).
