# OpenAI — design documents

> **Disclaimer.** This is independent reference material for studying software architecture. It has no affiliation with, and no endorsement by, OpenAI, Microsoft, Amazon / AWS, Google or any other company named, and it is not a substitute for any provider's products, documentation or terms. Product names are trademarks of their owners. Product facts change: they were checked on the date shown, against the sources listed in the last section; check them again before use. Provided "as is", without warranty; not legal, compliance, security or procurement advice. Full text: [DISCLAIMER.md](../DISCLAIMER.md).

OpenAI models reach an E2A service by four routes. They split by **who operates the service**, which decides the data processor, the identity, the billing and which hosted features exist. Hence four documents. Each records only what differs from the [core design](../core/E2A_MANAGED_PROVIDERS_CORE_DESIGN.md).

| Document | Operator | Agent runtime offered · remote MCP by the service · hosted file search | Status here | Word |
|---|---|---|---|---|
| [OpenAI API](OPENAI_API_DESIGN.md) | OpenAI | Agents API · yes · yes | Documented (the Responses wire is Implemented) | [docx](OpenAI_API_Design.docx) |
| [OpenAI in Microsoft Foundry](OPENAI_IN_MICROSOFT_FOUNDRY_DESIGN.md) | Microsoft | Foundry Agent Service · yes · Foundry tools | **Implemented** (`openai_azure`) | [docx](OpenAI_in_Microsoft_Foundry_Design.docx) |
| [OpenAI on Amazon Bedrock](OPENAI_ON_AMAZON_BEDROCK_DESIGN.md) | AWS | Bedrock Managed Agents (preview) · no · no | Documented | [docx](OpenAI_on_Amazon_Bedrock_Design.docx) |
| [OpenAI on Google Cloud](OPENAI_ON_GOOGLE_CLOUD_DESIGN.md) | Google Cloud perimeter (frontier models: private preview); gpt-oss-120b managed API (GA) | none · no (client-run only) · no | Documented | [docx](OpenAI_on_Google_Cloud_Design.docx) |

**What changes E2A's part:**
- **One wire everywhere.** All four offer OpenAI's Responses API, so `OpenAIResponsesLLMService` is written once and only the client (base URL, credential) changes. gpt-oss on Google uses Chat Completions.
- **No server-side refusal fallback** on any route: E2A's `model_routes` answer refusals.
- **Where the service cannot call MCP servers or search files** (Bedrock, Google), tools and retrieval run host-side through E2A and the cloud's services. E2A stays in every tool call path, which is the stricter shape.
- **Where the platform filters content itself** (Foundry's DefaultV2), E2A keeps its own stage for retrieved text and tool output. A platform block should be reported as `REFUSED`, not `FAILED` (follow-up).
- **Preview offerings** (Bedrock Managed Agents; OpenAI frontier models on Google Cloud) fail the replacement gate's "generally available" item: evaluation only, with a GA route configured.
- **In every variant, E2A keeps:**
  - the business idempotency key;
  - approval;
  - `_act` through `io_modify`;
  - `_confirm`;
  - tenant resolution at ingress.

**Follow-ups found by this review** (provider layer):
1. Classify OpenAI 429s for spend, credit or usage limits as non-retryable.
2. Map Foundry's `content_filter` (400, and `content_filters[].blocked` on output) to `REFUSED`.

All product facts were checked on 16 November 2026 against OpenAI's, Microsoft's, AWS's and Google's documentation; each document lists its sources.
