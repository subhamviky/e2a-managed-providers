# Google Gemini — design documents

> **Disclaimer.** This is independent reference material for studying software architecture. It has no affiliation with, and no endorsement by, Google, Amazon / AWS, Microsoft or any other company named, and it is not a substitute for any provider's products, documentation or terms. Product names are trademarks of their owners. Product facts change: they were checked on the date shown, against the sources listed in the last section; check them again before use. Provided "as is", without warranty; not legal, compliance, security or procurement advice. Full text: [DISCLAIMER.md](../DISCLAIMER.md).

Google operates Gemini on both routes, but the routes differ in identity (an API key, or Google Cloud IAM), terms, and the services around the model. Hence two documents. Use from AWS or Azure is a section of the Google Cloud document, because the platform, model and terms are the same; only identity and network change. Each document records only what differs from the [core design](../core/E2A_MANAGED_PROVIDERS_CORE_DESIGN.md).

| Document | Covers | Status here | Word |
|---|---|---|---|
| [Gemini Developer API](GEMINI_API_DESIGN.md) | API key; Interactions API and `generateContent`; managed agents (preview); remote MCP; File Search | Documented | [docx](Gemini_API_Design.docx) |
| [Gemini on Google Cloud](GEMINI_ON_GOOGLE_CLOUD_DESIGN.md) | Gemini Enterprise Agent Platform (formerly Vertex AI), from Google Cloud and from AWS or Azure through workload identity federation | **Implemented** (`google_vertex`); from AWS or Azure Documented | [docx](Gemini_on_Google_Cloud_Design.docx) |

**What changes E2A's part:**
- **The model lifecycle is now a design concern.** The profile's default, `gemini-2.5-pro`, was retired on Agent Platform on 20 October 2026. This step moves the default to `gemini-3.8-flash`.
- **Thinking levels differ per model.** E2A's effort is now lowered to a level each model accepts, instead of sending `XHIGH`, which most current models reject.
- **The Interactions API** is Google's recommended interface and stores interactions by default. E2A keeps the stateless, fully supported `generateContent` until it needs an Interactions-only capability (follow-up).
- **Safety filters default to Off** for Gemini 2.5 and later. E2A's governance stages (Model Armor on Google Cloud) are the control that is always on.
- **Preview offerings** (managed agents; Agent Gateway for Cloud Run) are not bound.
- **In every variant, E2A keeps:**
  - the business idempotency key;
  - approval;
  - `_act` through `io_modify`;
  - `_confirm`;
  - tenant resolution at ingress.

All product facts were checked on 16 November 2026 against Google's Gemini API and Google Cloud documentation; each document lists its sources.
