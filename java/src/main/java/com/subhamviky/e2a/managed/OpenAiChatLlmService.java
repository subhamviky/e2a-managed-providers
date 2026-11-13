package com.subhamviky.e2a.managed;

import com.openai.client.OpenAIClient;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import com.openai.models.chat.completions.ChatCompletion;
import com.openai.models.chat.completions.ChatCompletionCreateParams;
import com.subhamviky.e2a.core.RequestContext;
import com.subhamviky.e2a.llm.LlmResponse;
import com.subhamviky.e2a.llm.LlmService;

import java.util.List;
import java.util.Map;

/**
 * Meta Llama through a Llama Stack distribution's OpenAI-compatible chat completions,
 * using openai-java pointed at {@code <llama_stack_url>/v1}. The same class serves any
 * OpenAI-compatible managed endpoint.
 */
public final class OpenAiChatLlmService implements LlmService {

    private final OpenAIClient client;
    private final String defaultModel;

    public OpenAiChatLlmService(OpenAIClient client, String defaultModel) {
        this.client = client;
        this.defaultModel = defaultModel;
    }

    public static OpenAiChatLlmService llamaStack() {
        String url = ManagedSettings.get(null, "llama_stack_url", "LLAMA_STACK_URL", "http://llama-stack.internal:8321");
        OpenAIClient client = OpenAIOkHttpClient.builder()
                .baseUrl(url.replaceAll("/+$", "") + "/v1")
                .apiKey(ManagedSettings.get(null, "llama_stack_api_key", "LLAMA_STACK_API_KEY", "none"))
                .maxRetries(0)
                .build();
        return new OpenAiChatLlmService(client, ManagedSettings.get(null, "model_id", "LLM_MODEL_ID",
                "meta.llama4-maverick-17b-instruct-v1:0"));
    }

    @Override
    public LlmResponse infer(List<Map<String, String>> messages, RequestContext ctx) {
        ChatCompletionCreateParams.Builder params = ChatCompletionCreateParams.builder()
                .model(ManagedSettings.get(ctx, "model_id", "LLM_MODEL_ID", defaultModel))
                .maxCompletionTokens(ManagedSettings.getInt(ctx, "max_output_tokens", "LLM_MAX_OUTPUT_TOKENS", 4096));
        for (Map<String, String> m : messages) {
            String content = m.getOrDefault("content", "");
            switch (m.getOrDefault("role", "user")) {
                case "system" -> params.addSystemMessage(content);
                case "assistant" -> params.addAssistantMessage(content);
                default -> params.addUserMessage(content);
            }
        }
        ChatCompletion completion = client.chat().completions().create(params.build());
        ChatCompletion.Choice choice = completion.choices().get(0);
        if (choice.finishReason().equals(ChatCompletion.Choice.FinishReason.CONTENT_FILTER)) {
            throw new ModelRefusalException("meta", completion.model());
        }
        String text = choice.message().content().orElse("");
        int tokens = completion.usage().map(u -> (int) u.totalTokens()).orElse(0);
        return new LlmResponse(text, tokens);
    }
}
