package com.subhamviky.e2a.managed;

import com.google.genai.Client;
import com.google.genai.types.Content;
import com.google.genai.types.GenerateContentConfig;
import com.google.genai.types.GenerateContentResponse;
import com.google.genai.types.Part;
import com.subhamviky.e2a.core.RequestContext;
import com.subhamviky.e2a.llm.LlmResponse;
import com.subhamviky.e2a.llm.LlmService;

import java.util.List;
import java.util.Map;

/** Gemini on Vertex AI through the official google-genai Java SDK (Application Default Credentials). */
public final class GeminiLlmService implements LlmService {

    private final Client client;
    private final String defaultModel;

    public GeminiLlmService(Client client, String defaultModel) {
        this.client = client;
        this.defaultModel = defaultModel;
    }

    public static GeminiLlmService vertex() {
        Client client = Client.builder().vertexAI(true)
                .project(ManagedSettings.get(null, "gcp_project", "GOOGLE_CLOUD_PROJECT", null))
                .location(ManagedSettings.get(null, "gcp_location", "GOOGLE_CLOUD_LOCATION", "us-central1"))
                .build();
        return new GeminiLlmService(client, ManagedSettings.get(null, "model_id", "LLM_MODEL_ID", "gemini-2.5-pro"));
    }

    @Override
    public LlmResponse infer(List<Map<String, String>> messages, RequestContext ctx) {
        Conversation c = Conversation.of(messages);
        List<Content> contents = c.turns().stream().map(m -> Content.builder()
                .role(Conversation.isAssistant(m) ? "model" : "user")
                .parts(List.of(Part.fromText(m.getOrDefault("content", ""))))
                .build()).toList();
        GenerateContentConfig.Builder config = GenerateContentConfig.builder()
                .maxOutputTokens(ManagedSettings.getInt(ctx, "max_output_tokens", "LLM_MAX_OUTPUT_TOKENS", 4096));
        if (!c.system().isBlank()) {
            config.systemInstruction(Content.fromParts(Part.fromText(c.system())));
        }
        String model = ManagedSettings.get(ctx, "model_id", "LLM_MODEL_ID", defaultModel);
        GenerateContentResponse response = client.models.generateContent(model, contents, config.build());
        boolean blocked = response.candidates().flatMap(cs -> cs.stream().findFirst())
                .flatMap(cand -> cand.finishReason()).map(r -> r.toString().contains("SAFETY")).orElse(false);
        if (blocked) {
            throw new ModelRefusalException("google", model);
        }
        int tokens = response.usageMetadata().flatMap(u -> u.totalTokenCount()).orElse(0);
        return new LlmResponse(response.text() == null ? "" : response.text(), tokens);
    }
}
