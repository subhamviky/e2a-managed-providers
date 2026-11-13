import os

import pytest

PROFILES = ["anthropic_bedrock", "anthropic_claude_platform_aws", "openai_azure", "google_vertex", "meta_llama_aws"]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No test may depend on a developer's real cloud credentials or settings."""
    for key in list(os.environ):
        if key.startswith(("AWS_", "AZURE_", "GOOGLE_", "ANTHROPIC_", "OPENAI_", "LLAMA_", "E2A_", "LLM_", "MCP_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


SYSTEM = {"role": "system", "content": "You are the o2c router agent."}
USER = {"role": "user", "content": "What is our refund policy?"}
