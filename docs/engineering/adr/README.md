# Architecture Decision Records

One record per significant decision (context, options, decision, consequences). Records are not edited to change history: a reversed decision gets a new record that supersedes the old one. These are the decisions of e2a-managed-providers. Platform-wide decisions (a real cloud rather than emulators only, synthetic data, Workload Identity Federation, promotion, the documentation layout) are in the [assistant repository's ADR index](https://github.com/subhamviky/enterprise-finance-genai-assistant/blob/main/docs/engineering/adr/README.md).

Template: [`0000-template.md`](0000-template.md).

| # | Decision | Status |
|---|---|---|
| [0001](0001-frontier-provider-managed-services-behind-e2a.md) | Frontier-provider managed services behind E2A's vendor-neutral contracts, chosen by profile | Accepted |
| [0002](0002-configured-model-past-its-retirement-date.md) | A configured model past its retirement date fails | Accepted |
| [0003](0003-offline-wire-replay-tests-with-real-sdks-no.md) | Offline wire-replay tests with real SDKs; no provider accounts in CI | Accepted |
| [0004](0004-profile-bound-stores-are-tested-emulators.md) | Profile-bound stores are tested on the emulators, races included | Accepted |
| [0005](0005-gemini-api-profile-for-gemini-developer-api.md) | A `gemini_api` profile for the Gemini Developer API | Accepted |
| [0006](0006-consumers-pin-tag-fixes-may-sit-main-until-tag.md) | Consumers pin a tag; fixes may sit on `main` until a tag exists | Accepted |
