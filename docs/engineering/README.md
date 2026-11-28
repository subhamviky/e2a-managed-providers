# Engineering records: e2a-managed-providers

Frontier-provider managed services behind E2A's contracts (Anthropic, OpenAI, Google, Meta), chosen by profile.

| Area | Document |
|---|---|
| Decisions | [ADRs](adr/README.md) |
| Security | [Threat model](security/threat-model.md), [data handling](security/data-handling.md) |
| Delivery | [Release process](operations/release-process.md), [`CHANGELOG.md`](../../CHANGELOG.md) |
| Support | [Support and vulnerability handling](operations/support-and-vulnerabilities.md) |
| Quality | [Test strategy](testing/test-strategy.md) |
| Product | [Requirements and user stories](product/requirements-and-user-stories.md), [solution overview](product/solution-overview.md), [onboarding](product/onboarding.md) |

**Not applicable to a library, and why.** SLOs and an error budget, incident response, disaster recovery, cost and capacity, teardown and an HTTP API reference describe a *running service*. e2a-managed-providers is code that other projects install and pin to a tag: it has no deployment, no traffic and no data of its own. Their service-level equivalents are in the [assistant repository](https://github.com/subhamviky/enterprise-finance-genai-assistant/blob/main/docs/engineering/README.md) and the other service repositories. The library's public interface is documented in its design documents and its code (listed in the onboarding page).

Related: [E2A](https://github.com/subhamviky/e2a-framework) (the contracts) and [the assistant](https://github.com/subhamviky/enterprise-finance-genai-assistant) (the consumer that found the Gemma issue).

> **Status:** all documents here are drafts written on 28 November 2026 from the repository history, design documents, drills and tests, awaiting the owner's review. Where a date or a detail was not recorded, the document says so.
