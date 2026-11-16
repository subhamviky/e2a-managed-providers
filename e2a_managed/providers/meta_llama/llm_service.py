"""Kept for one release: Llama Stack was renamed OGX, and the profile moved to
e2a_managed.providers.ogx. These names point at the OGX classes. Llama Stack's shields API
was removed by OGX 1.x, so LlamaGuardStage has no successor here: the profile binds Bedrock
Guardrails, and OGX runs its own moderation inside the Responses loop."""
from ..ogx.llm_service import (OGXEmbeddingModel as LlamaStackEmbeddingModel,  # noqa: F401
                               OGXLLMService as LlamaStackLLMService,
                               OGXPromptRegistry as LlamaStackPromptRegistry,
                               OGXResponsesRuntime as LlamaStackResponsesRuntime,
                               OGXVectorIndex as LlamaStackVectorIndex)
