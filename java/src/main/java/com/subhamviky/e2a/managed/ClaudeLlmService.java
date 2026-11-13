package com.subhamviky.e2a.managed;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.models.messages.ContentBlock;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.OutputConfig;
import com.anthropic.models.messages.StopReason;
import com.subhamviky.e2a.core.RequestContext;
import com.subhamviky.e2a.llm.LlmResponse;
import com.subhamviky.e2a.llm.LlmService;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * Claude through the official anthropic-java SDK. The client decides the platform:
 * {@link #bedrock()} (Amazon Bedrock, Messages-API endpoint, AWS credential chain) or any
 * other configured AnthropicClient (Claude Platform on AWS, tests). No sampling
 * parameters are sent; depth is the effort level ({@code reasoning_effort}).
 */
public final class ClaudeLlmService implements LlmService {

    private final AnthropicClient client;
    private final String defaultModel;

    public ClaudeLlmService(AnthropicClient client, String defaultModel) {
        this.client = client;
        this.defaultModel = defaultModel;
    }

    /** Claude on Amazon Bedrock (Mantle backend): region and credentials from the AWS chain. */
    public static ClaudeLlmService bedrock() {
        AnthropicClient client = AnthropicOkHttpClient.builder()
                .backend(com.anthropic.bedrock.backends.BedrockMantleBackend.fromEnv())
                .maxRetries(0)                       // E2A's resilience owns retries
                .build();
        return new ClaudeLlmService(client, ManagedSettings.get(null, "model_id", "LLM_MODEL_ID",
                "anthropic.claude-opus-5-5"));
    }

    @Override
    public LlmResponse infer(List<Map<String, String>> messages, RequestContext ctx) {
        Conversation c = Conversation.of(messages);
        String model = ManagedSettings.get(ctx, "model_id", "LLM_MODEL_ID", defaultModel);
        MessageCreateParams.Builder params = MessageCreateParams.builder()
                .model(model)
                .maxTokens(ManagedSettings.getInt(ctx, "max_output_tokens", "LLM_MAX_OUTPUT_TOKENS", 4096));
        if (!c.system().isBlank()) {
            params.system(c.system());
        }
        for (Map<String, String> turn : c.turns()) {
            String content = turn.getOrDefault("content", "");
            if (Conversation.isAssistant(turn)) {
                params.addAssistantMessage(content);
            } else {
                params.addUserMessage(content);
            }
        }
        String effort = ManagedSettings.get(ctx, "reasoning_effort", "LLM_REASONING_EFFORT", "medium");
        params.outputConfig(OutputConfig.builder().effort(OutputConfig.Effort.of(effort)).build());

        Message message = client.messages().create(params.build());
        if (message.stopReason().isPresent() && message.stopReason().get().equals(StopReason.REFUSAL)) {
            throw new ModelRefusalException("anthropic", model);
        }
        String text = message.content().stream().filter(ContentBlock::isText)
                .map(b -> b.asText().text()).collect(Collectors.joining());
        int tokens = (int) (message.usage().inputTokens() + message.usage().outputTokens());
        return new LlmResponse(text, tokens);
    }
}
