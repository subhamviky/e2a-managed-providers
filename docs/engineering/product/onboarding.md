# Onboarding

Read: `README.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITY_MATRIX.md`, `docs/TESTING.md`, then the [ADRs](../adr/README.md) and `docs/design/DISCLAIMER.md`.

Run locally: install the package with the test extras and run `pytest` (offline replay needs no account); start the emulators to run the store tests as in `.github/workflows/ci.yml`.

Map: `e2a_managed/` (providers, profiles, bindings), `tests/`, `java/`, `docs/`. Working agreements: one concern per branch; CI green; no keys in the repository; record a retired model in the catalogue; tag a release before a consumer depends on a fix.
