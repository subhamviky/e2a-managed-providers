"""Every configuration key this package adds, registered in E2A's central registry.

E2A rule 1: no class reads os.getenv() or hard-codes a default; it calls
resolve_config(key, config, kwargs). The order is the framework's own and is never
changed here:

    call-time kwargs  >  config dict (the provider profile)  >  environment  >  default

So any vendor-managed parameter (an effort level, a guardrail version, a deployment
name) can be passed as a keyword to the inherited public method of the class that uses
it (BaseAgent.run(), process_inference(), retrieve(), execute() ...), and that value
wins over the profile and the environment for that one call.

Keys are registered when this module is imported (e2a_managed/__init__ imports it).
"""
import json
from typing import Any, List

from e2a import ConfigKey, register_config_key


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "yes", "on")


def _list(v: Any) -> List[Any]:
    if isinstance(v, (list, tuple)):
        return list(v)
    text = str(v).strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [x.strip() for x in text.split(",") if x.strip()]


def _opt_int(v: Any):
    return None if v in (None, "") else int(v)


_COMMON = "Managed providers — common"
_AWS = "Managed providers — Anthropic / Meta on AWS"
_AZURE = "Managed providers — OpenAI on Azure"
_GCP = "Managed providers — Google on Vertex AI"
_META = "Managed providers — Meta Llama Stack"

MANAGED_KEYS = [
    # ---- common: profile selection and who owns which runtime concern
    ConfigKey("provider_profile", "E2A_PROVIDER_PROFILE", None, str, _COMMON,
              "bindings_from_profile() — which provider profile to bind (e.g. anthropic_bedrock)"),
    ConfigKey("llm_sdk_max_retries", "LLM_SDK_MAX_RETRIES", 0, int, _COMMON,
              "Provider SDK retries. 0: E2A (v1.5.1+) retries model calls by error class, honouring "
              "Retry-After within the deadline, so it is the only retry authority (no double retry)"),
    ConfigKey("llm_sdk_timeout", "LLM_SDK_TIMEOUT", 60.0, float, _COMMON,
              "ManagedLLMService — SDK HTTP timeout; the E2A deadline still bounds every call"),
    ConfigKey("reasoning_effort", "LLM_REASONING_EFFORT", None, str, _COMMON,
              "ManagedLLMService — provider reasoning depth (low|medium|high...), mapped per provider"),
    ConfigKey("fallback_model_id", "LLM_FALLBACK_MODEL_ID", None, str, _COMMON,
              "ManagedLLMService — model retried once when the primary model declines (refusal)"),
    ConfigKey("mcp_servers", "MCP_SERVERS", [], _list, _COMMON,
              "provider MCP connector — [{name, url, token_env}] remote MCP servers the model may call"),
    ConfigKey("embedding_model_id", "EMBEDDING_MODEL_ID", None, str, _COMMON,
              "Managed embedding models — provider model / deployment id"),
    ConfigKey("embedding_dimensions", "EMBEDDING_DIMENSIONS", None, _opt_int, _COMMON,
              "Managed embedding models — output dimensions where the model supports it"),
    ConfigKey("vector_index_name", "VECTOR_INDEX_NAME", "e2a-knowledge", str, _COMMON,
              "Managed vector indexes — index / collection name (the tenant namespace is added)"),
    ConfigKey("rerank_model_id", "RERANK_MODEL_ID", None, str, _COMMON,
              "Managed rerankers — provider rerank model"),
    ConfigKey("guardrail_fields", "GUARDRAIL_FIELDS", ["query"], _list, _COMMON,
              "ManagedGuardrailStage — state fields screened by the provider guardrail"),
    ConfigKey("delegated_runtime_id", "DELEGATED_RUNTIME_ID", None, str, _COMMON,
              "Delegated runtimes — managed agent id / runtime ARN / engine resource"),
    ConfigKey("delegated_runtime_environment", "DELEGATED_RUNTIME_ENVIRONMENT", None, str, _COMMON,
              "Delegated runtimes — environment / endpoint qualifier"),
    ConfigKey("delegated_max_turns", "DELEGATED_MAX_TURNS", 8, int, _COMMON,
              "Delegated runtimes — host-side tool round trips before the run is stopped"),
    ConfigKey("otel_service_name", "OTEL_SERVICE_NAME", "e2a-service", str, _COMMON,
              "ManagedObservability — service.name resource attribute"),
    ConfigKey("otlp_endpoint", "OTEL_EXPORTER_OTLP_ENDPOINT", None, str, _COMMON,
              "ManagedObservability — OTLP collector (ADOT, Azure Monitor agent, Cloud Trace collector)"),

    # ---- AWS (Anthropic on Bedrock / Claude Platform on AWS, Meta on AWS)
    ConfigKey("aws_region", "AWS_REGION", "us-east-1", str, _AWS, "every AWS client"),
    ConfigKey("anthropic_aws_workspace_id", "ANTHROPIC_AWS_WORKSPACE_ID", None, str, _AWS,
              "ClaudePlatformAWSLLMService — workspace of Claude Platform on AWS"),
    ConfigKey("anthropic_betas", "ANTHROPIC_BETAS", [], _list, _AWS,
              "Claude adapters — extra beta flags (e.g. mcp-client-2025-11-20)"),
    ConfigKey("bedrock_guardrail_id", "BEDROCK_GUARDRAIL_ID", None, str, _AWS, "BedrockGuardrailStage"),
    ConfigKey("bedrock_guardrail_version", "BEDROCK_GUARDRAIL_VERSION", "DRAFT", str, _AWS, "BedrockGuardrailStage"),
    ConfigKey("bedrock_knowledge_base_id", "BEDROCK_KNOWLEDGE_BASE_ID", None, str, _AWS,
              "BedrockKnowledgeBaseRAGPipeline — fully managed retrieval"),
    ConfigKey("s3_vector_bucket", "S3_VECTOR_BUCKET", None, str, _AWS, "S3VectorsIndex"),
    ConfigKey("bedrock_prompt_arn_prefix", "BEDROCK_PROMPT_ARN_PREFIX", None, str, _AWS,
              "BedrockPromptRegistry — prompt ids are resolved under this ARN prefix"),
    ConfigKey("agentcore_gateway_url", "AGENTCORE_GATEWAY_URL", None, str, _AWS,
              "AgentCore Gateway — the managed MCP endpoint in front of the E2A MCP servers"),
    ConfigKey("dynamodb_idempotency_table", "DYNAMODB_IDEMPOTENCY_TABLE", "e2a-idempotency", str, _AWS,
              "DynamoDBIdempotencyStore"),
    ConfigKey("dynamodb_checkpoint_table", "DYNAMODB_CHECKPOINT_TABLE", "e2a-checkpoints", str, _AWS,
              "DynamoDBCheckpointStore"),

    # ---- Azure (OpenAI on Azure AI Foundry)
    ConfigKey("azure_openai_endpoint", "AZURE_OPENAI_ENDPOINT", None, str, _AZURE,
              "Azure OpenAI v1 API — https://<resource>.openai.azure.com (the /openai/v1/ route is added)"),
    ConfigKey("azure_openai_api_key", "AZURE_OPENAI_API_KEY", None, str, _AZURE,
              "Azure OpenAI — key auth; leave unset to use Entra ID (managed identity)", secret=True),
    ConfigKey("openai_store_responses", "OPENAI_STORE_RESPONSES", False, _bool, _AZURE,
              "Responses API `store` — False keeps no server-side copy unless a delegated runtime needs it"),
    ConfigKey("azure_search_endpoint", "AZURE_SEARCH_ENDPOINT", None, str, _AZURE, "AzureAISearchIndex"),
    ConfigKey("azure_search_api_key", "AZURE_SEARCH_API_KEY", None, str, _AZURE, "AzureAISearchIndex", secret=True),
    ConfigKey("azure_search_semantic_config", "AZURE_SEARCH_SEMANTIC_CONFIG", "default", str, _AZURE,
              "AzureAISearchIndex — semantic ranker configuration"),
    ConfigKey("azure_content_safety_endpoint", "AZURE_CONTENT_SAFETY_ENDPOINT", None, str, _AZURE,
              "AzureContentSafetyStage — Prompt Shields + text analysis"),
    ConfigKey("azure_content_safety_key", "AZURE_CONTENT_SAFETY_KEY", None, str, _AZURE,
              "AzureContentSafetyStage", secret=True),
    ConfigKey("azure_apim_subscription_key", "AZURE_APIM_SUBSCRIPTION_KEY", None, str, _AZURE,
              "API Management as MCP gateway", secret=True),
    ConfigKey("cosmos_endpoint", "COSMOS_ENDPOINT", None, str, _AZURE, "CosmosIdempotencyStore"),
    ConfigKey("cosmos_database", "COSMOS_DATABASE", "e2a", str, _AZURE, "CosmosIdempotencyStore"),

    # ---- Google Cloud (Gemini on Vertex AI)
    ConfigKey("gcp_project", "GOOGLE_CLOUD_PROJECT", None, str, _GCP, "every Google client"),
    ConfigKey("gcp_location", "GOOGLE_CLOUD_LOCATION", "us-central1", str, _GCP, "every Google client"),
    ConfigKey("vertex_index_endpoint", "VERTEX_INDEX_ENDPOINT", None, str, _GCP,
              "VertexVectorSearchIndex — projects/../indexEndpoints/.. (query)"),
    ConfigKey("vertex_deployed_index_id", "VERTEX_DEPLOYED_INDEX_ID", None, str, _GCP, "VertexVectorSearchIndex"),
    ConfigKey("vertex_index", "VERTEX_INDEX", None, str, _GCP,
              "VertexVectorSearchIndex — projects/../indexes/.. (streaming upsert)"),
    ConfigKey("vertex_ranking_config", "VERTEX_RANKING_CONFIG", "default_ranking_config", str, _GCP,
              "VertexRankingReranker — Vertex AI Ranking API config"),
    ConfigKey("model_armor_template", "MODEL_ARMOR_TEMPLATE", None, str, _GCP,
              "ModelArmorStage — projects/../locations/../templates/.."),
    ConfigKey("vertex_agent_engine", "VERTEX_AGENT_ENGINE", None, str, _GCP,
              "VertexAgentEngineRuntime — projects/../reasoningEngines/.."),
    ConfigKey("firestore_database", "FIRESTORE_DATABASE", "(default)", str, _GCP, "FirestoreIdempotencyStore"),

    # ---- Meta (Llama Stack distribution)
    ConfigKey("llama_stack_url", "LLAMA_STACK_URL", "http://localhost:8321", str, _META,
              "every Llama Stack client"),
    ConfigKey("llama_stack_api_key", "LLAMA_STACK_API_KEY", None, str, _META, "Llama Stack auth", secret=True),
    ConfigKey("llama_shield_id", "LLAMA_SHIELD_ID", "llama-guard", str, _META, "LlamaGuardStage"),
    ConfigKey("llama_vector_store_id", "LLAMA_VECTOR_STORE_ID", None, str, _META, "LlamaStackVectorIndex"),
]

for _spec in MANAGED_KEYS:
    register_config_key(_spec)
