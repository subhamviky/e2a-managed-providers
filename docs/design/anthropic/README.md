# Anthropic (Claude) — design documents

> **Disclaimer.** This is independent reference material for studying software architecture. It has no affiliation with, and no endorsement by, Anthropic, Amazon / AWS, Google, Microsoft or any other company named, and it is not a substitute for any provider's products, documentation or terms. Product names are trademarks of their owners. Product facts change: they were checked on the date shown, against the sources listed in the last section; check them again before use. Provided "as is", without warranty; not legal, compliance, security or procurement advice. Full text: [DISCLAIMER.md](../DISCLAIMER.md).

Claude reaches an E2A service by several routes. They split cleanly by **who operates the service**, which decides the data processor, the identity, the billing, and which managed features exist. Hence four documents. Each records only what differs from the [core design](../core/E2A_MANAGED_PROVIDERS_CORE_DESIGN.md).

| Document | Variants | Operator | Managed Agents · MCP connector · server-side fallback | Status here | PDF |
|---|---|---|---|---|---|
| [Claude operated by Anthropic](ANTHROPIC_OPERATED_CLAUDE_DESIGN.md) | Claude API · Claude Platform on AWS · Claude Models on Google Cloud (Anthropic managed) | Anthropic | yes · yes · Claude API and Google Cloud only (beta) | Platform on AWS Implemented; the others Documented | [PDF](Anthropic_Operated_Claude_Design.pdf) |
| [Claude on Amazon Bedrock](CLAUDE_ON_AMAZON_BEDROCK_DESIGN.md) | `bedrock-mantle` (Messages API) · `bedrock-runtime` (InvokeModel / Converse) | AWS | no · no · no | Implemented (`anthropic_bedrock`) | [PDF](Claude_on_Amazon_Bedrock_Design.pdf) |
| [Claude on Google Cloud (Google managed)](CLAUDE_ON_GOOGLE_CLOUD_DESIGN.md) | Gemini Enterprise Agent Platform | Google | no · no · no | Documented | [PDF](Claude_on_Google_Cloud_Design.pdf) |
| [Claude in Microsoft Foundry](CLAUDE_IN_MICROSOFT_FOUNDRY_DESIGN.md) | Hosted on Azure · Hosted on Anthropic | Anthropic (on Azure or on Anthropic infrastructure) | no · yes (beta) · no | Documented | [PDF](Claude_in_Microsoft_Foundry_Design.pdf) |

**What changes E2A's part:**
- **Where Claude Managed Agents and the MCP connector exist** (Anthropic-operated), a read-only agent loop and tool transport can be delegated. **Where they don't** (Bedrock, Google managed, Foundry), the cloud's agent runtime and gateway take that role, or E2A's agent keeps the loop.
- **Where server-side fallback is missing**, E2A's `model_routes` answer refusals.
- **Where the platform has no content filter for Claude** (Foundry), the cloud's safety service becomes a required E2A governance stage.
- **In every variant, E2A keeps:**
  - the business idempotency key;
  - approval;
  - `_act` through `io_modify`;
  - `_confirm`;
  - tenant resolution at ingress.

All product facts were checked on 16 November 2026 against Anthropic's, AWS's, Google's and Microsoft's documentation; each document lists its sources.
