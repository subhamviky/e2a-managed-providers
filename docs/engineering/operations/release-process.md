# Release process: e2a-managed-providers

1. **Branch and pull request.** CI (`ci`): DynamoDB Local and the Firestore and Cosmos DB emulators start; unit, contract and offline wire-replay tests for every provider with the real SDKs; every profile binds and provides its features; the model lifecycle test (a configured model past its retirement date fails); then, per profile, the project test suites and the landscape scenario offline; `terraform test` with mock providers.
2. **Merge to `main`.**
3. **Tag `vX.Y.Z`.** Consumers pin the tag. Current tag: **v0.2.0**. The Gemma no-thinking-level fix is on `main` and **not yet in a tag**; the assistant installs from `main` until `v0.2.1` is cut. A tag is never moved.
4. **Rollback.** Consumers repin the previous tag.

Compatibility: depends on a specific E2A tag (stated in the repository); a change of E2A's contracts is a coordinated release.
