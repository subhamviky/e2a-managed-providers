"""pytest plugin: run a project's existing test suite against a managed provider.

    E2A_PROVIDER_PROFILE=openai_azure \
    E2A_SWAP_FACTORIES=o2c_agent_service.services.llm:build_o2c_llm \
    pytest -p e2a_managed.pytest_plugin <project>/tests

Before the project's conftest imports its LLM factory, the factory is wrapped (see
e2a_managed.swap): every model call then goes through the provider's real SDK. With
E2A_SWAP_LIVE=true the calls go to the provider for real (credentials required).
"""
import os


def pytest_configure(config):
    profile = os.environ.get("E2A_PROVIDER_PROFILE")
    targets = [t.strip() for t in os.environ.get("E2A_SWAP_FACTORIES", "").split(",") if t.strip()]
    if not (profile and targets):
        return
    from .swap import wrap_factory
    live = os.environ.get("E2A_SWAP_LIVE", "").lower() in ("1", "true", "yes")
    for target in targets:
        wrap_factory(target, profile, live=live)
    config.stash_e2a_profile = profile


def pytest_report_header(config):
    profile = os.environ.get("E2A_PROVIDER_PROFILE")
    if profile and os.environ.get("E2A_SWAP_FACTORIES"):
        mode = "LIVE" if os.environ.get("E2A_SWAP_LIVE", "").lower() in ("1", "true", "yes") else "offline replay"
        return f"e2a-managed: model calls bound to profile '{profile}' ({mode})"
