package com.subhamviky.e2a.managed;

import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.subhamviky.e2a.llm.LlmService;

/**
 * The Java composition root: the same profile names as the Python package.
 * <pre>
 *   E2AConfig config = E2AConfig.empty().withLlm(ManagedLlmServices.fromEnvironment());
 * </pre>
 */
public final class ManagedLlmServices {

    private ManagedLlmServices() {
    }

    public static LlmService fromEnvironment() {
        String profile = System.getenv("E2A_PROVIDER_PROFILE");
        if (profile == null || profile.isBlank()) {
            throw new IllegalStateException("set E2A_PROVIDER_PROFILE");
        }
        return fromProfile(profile);
    }

    public static LlmService fromProfile(String profile) {
        return switch (profile) {
            case "anthropic_bedrock" -> ClaudeLlmService.bedrock();
            case "anthropic_claude_platform_aws" -> new ClaudeLlmService(AnthropicOkHttpClient.builder()
                    .backend(com.anthropic.aws.backends.AwsBackend.fromEnv()).maxRetries(0).build(),
                    ManagedSettings.get(null, "model_id", "LLM_MODEL_ID", "claude-opus-5-5"));
            case "openai_azure" -> OpenAiResponsesLlmService.azure();
            case "google_vertex" -> GeminiLlmService.vertex();
            case "meta_llama_aws" -> OpenAiChatLlmService.llamaStack();
            default -> throw new IllegalArgumentException("unknown provider profile: " + profile);
        };
    }
}
