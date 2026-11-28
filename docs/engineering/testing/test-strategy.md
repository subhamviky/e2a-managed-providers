# Test strategy: e2a-managed-providers

| Layer | What it proves |
|---|---|
| Unit and contract | each provider's models, embeddings, vector search, rerankers, guardrails and delegated runtimes honour E2A's contracts |
| Offline wire replay | real SDKs against recorded exchanges: no accounts, deterministic |
| Profile checks | every profile binds, and provides the features it claims |
| Model lifecycle | a configured model past its retirement date fails the build |
| Stores on emulators | DynamoDB Local, Firestore, Cosmos DB: the stores' contract, races included |
| Project suites per profile | the projects' own tests run on each profile, offline |
| Landscape scenario per profile | the five-service scenario, offline |
| Live probe | `landscape/probe_model.py` in the assistant repository calls each model with the features the adapter sends (found the Gemma thinking-level rejection) |

Gaps: live provider behaviour is sampled by probes and evaluations, not by CI; retired or changed models are caught only if the catalogue is current.
