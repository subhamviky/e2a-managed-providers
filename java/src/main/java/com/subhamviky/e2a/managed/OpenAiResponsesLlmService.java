package com.subhamviky.e2a.managed;

import com.openai.client.OpenAIClient;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import com.openai.models.Reasoning;
import com.openai.models.ReasoningEffort;
import com.openai.models.responses.EasyInputMessage;
import com.openai.models.responses.Response;
import com.openai.models.responses.ResponseCreateParams;
import com.openai.models.responses.ResponseInputItem;
import com.subhamviky.e2a.core.RequestContext;
import com.subhamviky.e2a.llm.LlmResponse;
import com.subhamviky.e2a.llm.LlmService;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * OpenAI models on Azure AI Foundry through the official openai-java SDK and the Azure
 * v1 API ({@code https://<resource>.openai.azure.com/openai/v1}, deployment name as model).
 */
public final class OpenAiResponsesLlmService implements LlmService {

    private final OpenAIClient client;
    private final String defaultModel;

    public OpenAiResponsesLlmService(OpenAIClient client, String defaultModel) {
        this.client = client;
        this.defaultModel = defaultModel;
    }

    /** Azure OpenAI v1 endpoint; the key comes from AZURE_OPENAI_API_KEY (Entra ID in production). */
    public static OpenAiResponsesLlmService azure() {
        String endpoint = ManagedSettings.get(null, "azure_openai_endpoint", "AZURE_OPENAI_ENDPOINT", "");
        OpenAIClient client = OpenAIOkHttpClient.builder()
                .baseUrl(endpoint.replaceAll("/+$", "") + "/openai/v1")
                .apiKey(ManagedSettings.get(null, "azure_openai_api_key", "AZURE_OPENAI_API_KEY", ""))
                .maxRetries(0)
                .build();
        return new OpenAiResponsesLlmService(client, ManagedSettings.get(null, "model_id", "LLM_MODEL_ID", "gpt-5"));
    }

    @Override
    public LlmResponse infer(List<Map<String, String>> messages, RequestContext ctx) {
        Conversation c = Conversation.of(messages);
        List<ResponseInputItem> input = new ArrayList<>();
        for (Map<String, String> turn : c.turns()) {
            input.add(ResponseInputItem.ofEasyInputMessage(EasyInputMessage.builder()
                    .role(Conversation.isAssistant(turn) ? EasyInputMessage.Role.ASSISTANT : EasyInputMessage.Role.USER)
                    .content(turn.getOrDefault("content", ""))
                    .build()));
        }
        ResponseCreateParams.Builder params = ResponseCreateParams.builder()
                .model(ManagedSettings.get(ctx, "model_id", "LLM_MODEL_ID", defaultModel))
                .inputOfResponse(input)
                .store(false)
                .maxOutputTokens(ManagedSettings.getInt(ctx, "max_output_tokens", "LLM_MAX_OUTPUT_TOKENS", 4096))
                .reasoning(Reasoning.builder().effort(ReasoningEffort.of(
                        ManagedSettings.get(ctx, "reasoning_effort", "LLM_REASONING_EFFORT", "medium"))).build());
        if (!c.system().isBlank()) {
            params.instructions(c.system());
        }
        Response response = client.responses().create(params.build());
        StringBuilder text = new StringBuilder();
        response.output().forEach(item -> item.message().ifPresent(m -> m.content().forEach(part -> {
            part.outputText().ifPresent(t -> text.append(t.text()));
            part.refusal().ifPresent(r -> {
                throw new ModelRefusalException("openai", response.model().toString());
            });
        })));
        int tokens = response.usage().map(u -> (int) u.totalTokens()).orElse(0);
        return new LlmResponse(text.toString(), tokens);
    }
}
