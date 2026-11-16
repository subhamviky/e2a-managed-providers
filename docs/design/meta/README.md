# Meta Llama — design documents

> **Disclaimer.** This is independent reference material for studying software architecture. It has no affiliation with, and no endorsement by, Meta, Amazon / AWS, Google, Microsoft, the OGX project or any other company or project named, and it is not a substitute for any provider's products, documentation or terms. Product names are trademarks of their owners. Product facts change: they were checked on the date shown, against the sources listed in the last section; check them again before use. Provided "as is", without warranty; not legal, compliance, security or procurement advice. Full text: [DISCLAIMER.md](../DISCLAIMER.md).

The plan for this series assumed Meta offered a managed stack around Llama. Checking the official pages on 16 November 2026 changed that picture:
- **Llama Stack is now OGX.** It was renamed in April 2026, is model-neutral, and is run by the customer.
- **Managed Llama endpoints are being wound down.** Each cloud has published end-of-life dates for them.
- **Meta's own developer pages** render only in a browser, so they could not be checked here.

So the series has two documents, split by **who operates** the model endpoint. Each records only what differs from the [core design](../core/E2A_MANAGED_PROVIDERS_CORE_DESIGN.md).

| Document | Operator | Status here | Word |
|---|---|---|---|
| [Llama on cloud AI platforms](LLAMA_ON_CLOUD_PLATFORMS_DESIGN.md) | AWS (Bedrock), Google (Agent Platform), Microsoft (Foundry) | Documented; Bedrock Llama used today through OGX | [docx](Llama_on_Cloud_Platforms_Design.docx) |
| [OGX (formerly Llama Stack) and self-hosted Llama](OGX_SELF_HOSTED_LLAMA_DESIGN.md) | the customer | Implemented (`ogx_aws` on OGX 1.x; `meta_llama_aws` kept as an alias) | [docx](OGX_Self_Hosted_Llama_Design.docx) |

**What changes E2A's part:**
- **Lifecycle first.** Azure retires Llama 4 Maverick on 19 November 2026, and Bedrock's Llama models are Legacy until 30 March 2027. A release-pipeline lifecycle check and a non-Llama `model_routes` entry are the design.
- **The profile is migrated to OGX 1.x** as `ogx_aws`: the OpenAI SDK against OGX's stable endpoints, vector-store search instead of `vector_io`, and Bedrock Guardrails in place of the removed shields. `meta_llama_aws` stays as an alias for one release.
- **Meta's own APIs are not covered** until their official documentation can be read and shows a generally available service.
- **In every variant, E2A keeps:**
  - the business idempotency key;
  - approval;
  - `_act` through `io_modify`;
  - `_confirm`;
  - tenant resolution at ingress.

All product facts were checked on 16 November 2026; each document lists its sources.
