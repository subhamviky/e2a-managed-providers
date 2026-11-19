# Design documents — managed providers in E2A

> **Disclaimer.** Independent reference material for studying software architecture. There is no affiliation with, and no endorsement by, any company named, and nothing here is a substitute for any provider's products, documentation or terms. Product names are trademarks of their owners. Facts change: check the provider's current documentation. Provided "as is"; not legal, compliance, security or procurement advice. Full text: [DISCLAIMER.md](DISCLAIMER.md).

These documents show how the managed services of model providers and clouds fit the E2A framework. Each responsibility is decided by one rule:
- **replace** E2A's responsibility when the managed service meets every point of the replacement gate;
- **extend**, **adapt** or **compose** it when the service covers only part of it;
- **retain** E2A's handling when no managed service provides it.

They are reference documents, not product guides.

## How to read them

1. Start with the **core design**. It defines the decision model, the four owners, the seams, the E2A responsibilities written once, and the sequences shared by every provider.
2. Then read a provider's folder. Each provider document records only what differs from the core for that provider and hosting pattern.

Each document exists as Markdown (read here) and as a PDF beside it (for reading on any device, and for sharing).

| Document | Contents | PDF |
|---|---|---|
| [Core design](core/E2A_MANAGED_PROVIDERS_CORE_DESIGN.md) | Disposition model and replacement gate; HLD (owners, hosting patterns, capability packs); LLD (seams, profile composition, kernel dispositions); common sequences; failure, security and evidence models | [PDF](core/E2A_Managed_Providers_Core_Design.pdf) |
| [Anthropic](anthropic/README.md) | Four documents by operator: Claude operated by Anthropic (Claude API, Claude Platform on AWS, Google Cloud Anthropic managed); Claude on Amazon Bedrock; Claude on Google Cloud (Google managed); Claude in Microsoft Foundry | in the folder |
| [OpenAI](openai/README.md) | Four documents by operator: OpenAI API (OpenAI operates); OpenAI in Microsoft Foundry (Implemented, `openai_azure`); OpenAI on Amazon Bedrock; OpenAI on Google Cloud | in the folder |
| [Google Gemini](gemini/README.md) | Two documents: Gemini Developer API; Gemini on Google Cloud (Implemented, `google_vertex`), including use from AWS and Azure | in the folder |
| [Meta Llama](meta/README.md) | Two documents: Llama on cloud AI platforms (Bedrock, Agent Platform, Foundry); OGX (formerly Llama Stack) and self-hosted Llama (Implemented, `ogx_aws`) | in the folder |

## Evidence labels

| Label | Meaning |
|---|---|
| **Implemented** | In this repository or E2A, exercised by contract tests, emulators or offline replay (not proof of production behaviour) |
| **E2A contract** | E2A's port exists with a contract suite; the binding is described, not implemented here |
| **Documented** | Described by the provider's own documentation (source and date given); not implemented here |
| **To validate** | Logically sound, not stated as supported by the provider; confirm before use |

## Diagrams

The figures are PNG files rendered from sources in each `diagrams/` folder. The PlantUML sources share `common/style.puml`; the Graphviz sources carry their style inline. To render them:

```bash
dot -Tpng -Gdpi=220 diagrams/<name>.dot -o images/<name>.png
```

```bash
java -jar plantuml.jar -tpng -o ../images diagrams/<name>.puml
```
