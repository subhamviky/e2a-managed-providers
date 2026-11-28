# Solution overview

*One page.*

**The problem.** Each provider ships managed services (models, embeddings, vector stores, rerankers, guardrails, agents) with its own SDK; code that calls them directly is locked to one provider and repeats governance.

**What it is.** A layer that implements E2A's vendor-neutral contracts for Anthropic, OpenAI, Google and Meta, with profiles chosen by configuration and design documents that say, per provider, what the managed service replaces, extends or leaves to E2A. A `gemini_api` profile runs the cloud landscape on the free tier.

**Evidence.** CI binds every profile and replays recorded exchanges against the real SDKs; a live probe found that a Gemma model rejects a thinking level, which was fixed and is waiting for a tag.

**Limits.** Independent reference material (see the disclaimer): not affiliated with any provider; live behaviour is sampled, not covered; one maintainer.
