# Testing without accounts

| Level | What runs | What it proves |
|---|---|---|
| 1. Contract | profiles load, every seam binds lazily, capability check, E2A conformance | configuration alone selects a provider; the package follows E2A's rules |
| 2. Wire replay | each **real provider SDK** against `ReplayTransport` (`e2a_managed/offline.py`) | request mapping, auth signing, response/stream/tool-call parsing, per-call overrides |
| 3. Service models | AWS calls through botocore `Stubber` (validates against AWS's own API definitions); DynamoDB on moto | AWS request shapes are valid, not just plausible |
| 4. REST shapes | Azure AI Search / Content Safety, Vertex Vector Search / Ranking / Model Armor / Agent Engine, Llama Stack via httpx mocks | documented request bodies and response parsing |
| 5. Store contract | the same claim/checkpoint tests on DynamoDB, Cosmos DB (ETag fake) and Firestore (precondition fake) | E2A's exactly-once semantics on each cloud |
| 6. Project suites | the three projects' own pytest suites with `-p e2a_managed.pytest_plugin` | no project code changes; agents behave identically behind every provider |
| 7. IaC | `terraform fmt`, `validate`, `test` with `mock_provider` | the landing zone and managed-AI resources plan cleanly |
| 8. Java | each `LlmService` through anthropic-java / openai-java / google-genai against a local HTTP replay server | the Java gateway maps E2A messages and settings correctly |
| 9. Live (opt-in) | `examples/live_smoke.py`, `E2A_SWAP_LIVE=true` | real service behaviour: needs credentials |

## How the project suites are swapped

```bash
E2A_PROVIDER_PROFILE=google_vertex \
E2A_SWAP_FACTORIES=o2c_agent_service.services.llm:build_o2c_llm \
pytest -p e2a_managed.pytest_plugin            # in order-to-cash-agentic-ai/o2c-agent-service
```

The plugin wraps the project's factory before its conftest imports it. The factory still
builds the project's `SimulatedLLMService`, and returns the provider's adapter around
it. The rules answer behind the provider's wire format, and anything the tests read from
the simulator (`_rules`, `usage_summary()`) is forwarded to it.
